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

        # Ensure the parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

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

        # Ensure parent directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        
        # Ensure parent directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(f"DB:{notebook_name}:{database_id}\n")


class EnexUploader(object):
    def __init__(
        self, wrapper, root_id, mode: str, done_file: Path | None, rules: Rules, rejected_tracker=None, unsupported_dir: Path | None = None, working_dir: Path | None = None
    ):
        self.wrapper = wrapper  # NotionAPIWrapper instance
        self.root_id = root_id  # Root page ID string
        self.mode = mode

        self.rules = rules

        self.done_hashes = DoneFile(done_file) if done_file else set()
        self.rejected_tracker = rejected_tracker
        self.unsupported_dir = unsupported_dir
        
        # Determine working directory for cache
        # Priority: explicit working_dir, done_file parent, unsupported_dir parent, cwd
        if working_dir:
            cache_dir = working_dir
        elif done_file and done_file.is_absolute():
            cache_dir = done_file.parent
        elif unsupported_dir and unsupported_dir.is_absolute():
            cache_dir = unsupported_dir.parent
        else:
            cache_dir = Path.cwd()
        
        logger.debug(f"Using cache directory: {cache_dir}")
        
        # Initialize exception tracker for partial imports
        self.exception_tracker = ExceptionTracker(wrapper, root_id, cache_dir) if wrapper else None

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

        # Initialize exception infrastructure before processing
        if self.exception_tracker:
            try:
                self.exception_tracker.initialize_infrastructure()
            except Exception as e:
                logger.warning(f"Failed to initialize exception infrastructure: {e}")
                # Continue with processing - infrastructure will be created on-demand if needed

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
            page_id, has_errors, updated_errors, failed_uploads, user_action_blocks = self._upload_note(self.notebook_root, note, note_blocks, errors, notebook_name)
            self.done_hashes.add(note.note_hash)
            
            # Track partial import in exception summary page
            # Filter out user-actionable errors (they go in the User Action Required database instead)
            if has_errors and self.exception_tracker:
                # Separate user-actionable errors from informational errors
                informational_errors = []
                
                for error in updated_errors:
                    # Skip user-actionable errors (they're in the database)
                    if any(pattern in error for pattern in [
                        "Invalid URL marked with broken-link icon",
                        "Invalid bookmark URL",
                        "Image embed missing source URL",
                        "File upload failed",
                        "saved to unsupported-files"
                    ]):
                        continue
                    informational_errors.append(error)
                
                # Only track if there are informational errors
                # User-actionable items are in the database, no need to reference them here
                if informational_errors:
                    self.exception_tracker.track_partial_import(
                        notebook_name=notebook_name,
                        note_title=note.title,
                        page_id=page_id,
                        errors=informational_errors
                    )
            
            # Find the block ID for the first user action marker (for file uploads)
            # We'll use the first marker block ID since file upload failures create markers
            first_marker_block_id = None
            if user_action_blocks:
                # Get the first marker block ID (sorted by index)
                first_marker_block_id = user_action_blocks.get(min(user_action_blocks.keys()))
            
            # Add failed file uploads to exceptions database
            if failed_uploads and self.exception_tracker:
                for idx, failed_file in enumerate(failed_uploads):
                    # Try to find the corresponding marker block for this file
                    # For now, use the first marker since we typically have one marker per file
                    block_id = list(user_action_blocks.values())[idx] if idx < len(user_action_blocks) else first_marker_block_id
                    self.exception_tracker.add_exception_to_database(
                        notebook_name=notebook_name,
                        note_title=note.title,
                        page_id=page_id,
                        error_type="File Upload Failed",
                        error_detail=f"{failed_file['filename']} saved to {failed_file['path']}",
                        block_id=block_id
                    )
            
            # Add other user-actionable warnings to database
            if updated_errors and self.exception_tracker:
                for error in updated_errors:
                    # Check for missing image URL warning
                    if "Image embed missing source URL" in error:
                        self.exception_tracker.add_exception_to_database(
                            notebook_name=notebook_name,
                            note_title=note.title,
                            page_id=page_id,
                            error_type="Invalid URL",
                            error_detail="Image embed missing source URL",
                            block_id=first_marker_block_id
                        )
                    # Check for invalid URL warnings (broken links)
                    elif "Invalid bookmark URL" in error or "Invalid URL marked with broken-link icon" in error:
                        # Extract the URL from the error message for better context
                        # Error format: "Invalid URL marked with broken-link icon: <url>"
                        url_detail = error.split(":", 1)[1].strip() if ":" in error else error[:150]
                        self.exception_tracker.add_exception_to_database(
                            notebook_name=notebook_name,
                            note_title=note.title,
                            page_id=page_id,
                            error_type="Invalid URL",
                            error_detail=url_detail[:150],  # Include the actual URL
                            block_id=None  # No block-level link available for inline URLs
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
