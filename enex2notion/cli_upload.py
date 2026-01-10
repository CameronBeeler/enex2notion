import logging
from pathlib import Path

from enex2notion.enex_parser import parse_all_notes
from enex2notion.enex_types import EvernoteNote
from enex2notion.enex_uploader import upload_note
from enex2notion.enex_uploader_modes import get_notebook_database, get_notebook_page
from enex2notion.exception_tracker import ExceptionTracker
from enex2notion.note_parser.note import parse_note
from enex2notion.summary_report import NotebookStats
from enex2notion.utils_exceptions import NoteUploadFailException
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)



class DoneFile(object):
    """Tracks uploaded notes and created databases to support resume.
    
    Format:
    - Lines starting with 'DB:' store database mappings: DB:notebook_name:database_id
    - Other lines are note hashes (40 char hex strings)
    """
    def __init__(self, path: Path):
        self.path = path
        self.done_hashes = set()
        self.databases = {}  # notebook_name -> database_id

        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DB:"):
                        # Parse database mapping: DB:notebook_name:database_id
                        parts = line.split(":", 2)
                        if len(parts) == 3:
                            notebook_name = parts[1]
                            database_id = parts[2]
                            self.databases[notebook_name] = database_id
                    elif line:  # Note hash
                        self.done_hashes.add(line)
        except FileNotFoundError:
            pass  # File doesn't exist yet, start fresh

    def __contains__(self, note_hash):
        return note_hash in self.done_hashes

    def add(self, note_hash):
        """Add a successfully uploaded note hash."""
        self.done_hashes.add(note_hash)

        with open(self.path, "a") as f:
            f.write(f"{note_hash}\n")
    
    def get_database(self, notebook_name):
        """Get the database ID for a notebook, if it exists.
        
        Returns:
            Database ID string, or None if not found
        """
        return self.databases.get(notebook_name)
    
    def add_database(self, notebook_name, database_id):
        """Record a database creation for a notebook."""
        self.databases[notebook_name] = database_id
        
        with open(self.path, "a") as f:
            f.write(f"DB:{notebook_name}:{database_id}\n")


class EnexUploader(object):
    def __init__(
        self, wrapper, root_id, mode: str, done_file: Path | None, rules: Rules, rejected_tracker=None, unsupported_dir: Path | None = None
    ):
        self.wrapper = wrapper  # NotionAPIWrapper instance
        self.root_id = root_id  # Root page ID string
        self.mode = mode

        self.rules = rules

        self.done_hashes = DoneFile(done_file) if done_file else set()
        self.rejected_tracker = rejected_tracker
        self.unsupported_dir = unsupported_dir
        
        # Initialize exception tracker for partial imports
        self.exception_tracker = ExceptionTracker(wrapper, root_id) if wrapper else None

        self.notebook_root = None
        self.notebook_schema = None  # Store database schema if in DB mode

    def upload_notebook(self, enex_file: Path, note_title: str | None = None, note_index: int | None = None) -> NotebookStats:
        """Process a single notebook using single-pass parsing.

        Args:
            enex_file: Path to ENEX file
            note_title: Optional filter to import only note with exact title
            note_index: Optional filter to import only note at 1-based index

        Returns:
            NotebookStats with results of the import
        """
        notebook_name = enex_file.stem
        
        if note_title:
            logger.info(f"Processing notebook '{notebook_name}' (filtering for note: '{note_title}')...")
        elif note_index:
            logger.info(f"Processing notebook '{notebook_name}' (filtering for note index: {note_index})...")
        else:
            logger.info(f"Processing notebook '{notebook_name}'...")

        # Initialize stats
        notebook_stats = NotebookStats(notebook_name=notebook_name, enex_file=enex_file)

        # Phase 1: Parse all notes in single pass (with optional filtering)
        logger.debug(f"Parsing ENEX file '{enex_file.name}'...")
        parse_stats = parse_all_notes(enex_file, note_title_filter=note_title, note_index_filter=note_index)
        notebook_stats.total = parse_stats.total

        logger.info(f"Parsed {parse_stats.total} notes: {parse_stats.successful} successful, {parse_stats.failed} failed")

        # Phase 2: Track parse failures
        # Note: Parse failures are now tracked in-band via partial imports
        # No separate export directory needed - all notes get Notion pages
        if parse_stats.failed > 0:
            notebook_stats.failed = parse_stats.failed

        # Phase 3: Get or create notebook root
        try:
            result = self._get_notebook_root(notebook_name)
            # Handle tuple return for DB mode (id, schema) or just id for PAGE mode
            if self.mode == "DB" and isinstance(result, tuple):
                self.notebook_root, self.notebook_schema = result
                # Store database ID in done file for future runs
                if isinstance(self.done_hashes, DoneFile):
                    self.done_hashes.add_database(notebook_name, self.notebook_root)
            else:
                self.notebook_root = result
                self.notebook_schema = None
        except NoteUploadFailException as e:
            # Notebook root creation failed - cannot continue with this notebook
            logger.error(f"Failed to create notebook root: {e}")
            # All notes in this notebook are considered failed
            notebook_stats.failed += parse_stats.successful
            return notebook_stats

        # Phase 4: Upload successfully parsed notes
        successful_results = [r for r in parse_stats.results if not r.failed and r.note]

        for idx, result in enumerate(successful_results, 1):
            note = result.note
            upload_result, skip_reason = self._upload_single_note(note, idx, parse_stats.total, notebook_name, result)

            if upload_result == "success":
                notebook_stats.successful += 1
            elif upload_result == "skipped":
                notebook_stats.skipped += 1
            elif upload_result == "failed":
                notebook_stats.failed += 1

        return notebook_stats

    def _upload_single_note(
        self, note: EvernoteNote, note_idx: int, total_notes: int, notebook_name: str, parse_result
    ) -> tuple[str, str | None]:
        """Upload a single note.

        Returns:
            Tuple of (status, skip_reason) where status is 'success', 'skipped', or 'failed'
            and skip_reason is a string if skipped, None otherwise
        """
        # Check if already uploaded
        if note.note_hash in self.done_hashes:
            skip_reason = "Already uploaded (found in done file)"
            logger.debug(f"Skipping note '{note.title}' ({skip_reason})")
            return "skipped", skip_reason

        # Add custom tag if specified
        if self.rules.tag and self.rules.tag not in note.tags:
            note.tags.append(self.rules.tag)

        # Parse note content
        logger.debug(f"Converting note '{note.title}' to Notion blocks")
        note_blocks, errors = self._parse_note(note)
        
        # Handle blank note names
        if not note.title or not note.title.strip():
            note.title = "[Untitled Note]"
            if "Note had no title in Evernote" not in str(errors):
                errors.append("Note had no title in Evernote - assigned default title")
        
        # Always upload even if no blocks (partial import)
        # The error summary will be added by upload_note

        # Upload to Notion
        logger.info(f"Uploading note {note_idx}/{total_notes}: '{note.title}'")

        try:
            page_id, has_errors, updated_errors = self._upload_note(self.notebook_root, note, note_blocks, errors, notebook_name)
            self.done_hashes.add(note.note_hash)
            
            # Track partial import in exception summary page (use updated errors with warnings)
            if has_errors and self.exception_tracker:
                self.exception_tracker.track_partial_import(
                    notebook_name=notebook_name,
                    note_title=note.title,
                    page_id=page_id,
                    errors=updated_errors
                )
            
            return "success", None
        except NoteUploadFailException as e:
            error_msg = str(e)
            logger.error(f"Failed to upload note '{note.title}': {error_msg}")
            # Continue with next note - error tracking handled via exception pages
            return "failed", None

    def _parse_note(self, note):
        """Parse note and return (blocks, errors) tuple."""
        try:
            return parse_note(note, self.rules)
        except Exception as e:
            logger.error(f"Failed to parse note '{note.title}'")
            logger.debug(e, exc_info=e)
            # Return empty blocks with error message
            return [], [f"Parse exception: {str(e)}"]

    def _get_notebook_root(self, notebook_title):
        if self.wrapper is None:
            return None

        # Check if we already have a database ID cached from a previous run
        if self.mode == "DB" and isinstance(self.done_hashes, DoneFile):
            cached_db_id = self.done_hashes.get_database(notebook_title)
            if cached_db_id:
                logger.info(f"Using existing database from progress file: {cached_db_id}")
                # Fetch the schema for the existing database
                try:
                    database = self.wrapper.get_database(cached_db_id)
                    schema = database.get("properties", {})
                    logger.debug(f"Retrieved schema with {len(schema)} properties")
                    return (cached_db_id, schema)
                except Exception as e:
                    logger.warning(f"Failed to retrieve cached database {cached_db_id}: {e}")
                    logger.warning("Will search for or create a new database")
                    # Continue to normal search/create flow

        error_message = f"Failed to get notebook root for '{notebook_title}'"
        get_func = get_notebook_database if self.mode == "DB" else get_notebook_page

        return self._attempt_upload(
            get_func, error_message, self.wrapper, self.root_id, notebook_title
        )

    def _upload_note(self, notebook_root, note, note_blocks, errors, notebook_name):
        return upload_note(
            self.wrapper,
            notebook_root,
            note,
            note_blocks,
            errors,
            is_database=(self.mode == "DB"),
            database_schema=self.notebook_schema,
            rejected_tracker=self.rejected_tracker,
            notebook_name=notebook_name,
            unsupported_dir=self.unsupported_dir,
        )

    def _attempt_upload(self, upload_func, error_message, *args, **kwargs):
        """Attempt upload with error wrapping."""
        try:
            return upload_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"{error_message}: {e}")
            raise NoteUploadFailException from e
