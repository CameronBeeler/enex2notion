import itertools
import logging
from pathlib import Path

from enex2notion.enex_parser import parse_all_notes
from enex2notion.enex_types import EvernoteNote
from enex2notion.enex_uploader import upload_note
from enex2notion.enex_uploader_modes import get_notebook_database, get_notebook_page
from enex2notion.failed_note_exporter import export_all_failed_notes
from enex2notion.note_parser.note import parse_note
from enex2notion.summary_report import NotebookStats
from enex2notion.utils_exceptions import NoteUploadFailException
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)


class DoneFile(object):
    def __init__(self, path: Path):
        self.path = path

        try:
            with open(path, "r") as f:
                self.done_hashes = {line.strip() for line in f}
        except FileNotFoundError:
            self.done_hashes = set()

    def __contains__(self, note_hash):
        return note_hash in self.done_hashes

    def add(self, note_hash):
        self.done_hashes.add(note_hash)

        with open(self.path, "a") as f:
            f.write(f"{note_hash}\n")


class EnexUploader(object):
    def __init__(
        self, import_root, mode: str, done_file: Path | None, rules: Rules, failed_export_dir: Path | None = None
    ):
        self.import_root = import_root
        self.mode = mode

        self.rules = rules

        self.done_hashes = DoneFile(done_file) if done_file else set()
        self.failed_export_dir = failed_export_dir or Path.cwd()

        self.notebook_root = None

    def upload_notebook(self, enex_file: Path) -> NotebookStats:
        """Process a single notebook using single-pass parsing.

        Returns:
            NotebookStats with results of the import
        """
        notebook_name = enex_file.stem
        logger.info(f"Processing notebook '{notebook_name}'...")

        # Initialize stats
        notebook_stats = NotebookStats(notebook_name=notebook_name, enex_file=enex_file)

        # Phase 1: Parse all notes in single pass
        logger.debug(f"Parsing ENEX file '{enex_file.name}'...")
        parse_stats = parse_all_notes(enex_file)
        notebook_stats.total = parse_stats.total

        logger.info(f"Parsed {parse_stats.total} notes: {parse_stats.successful} successful, {parse_stats.failed} failed")

        # Phase 2: Export failed notes immediately
        if parse_stats.failed > 0:
            failed_results = [r for r in parse_stats.results if r.failed]
            unimported_dir = export_all_failed_notes(failed_results, notebook_name, self.failed_export_dir, "failed")
            notebook_stats.failed_directory = unimported_dir
            notebook_stats.failed = parse_stats.failed

        # Phase 3: Get or create notebook root
        try:
            self.notebook_root = self._get_notebook_root(notebook_name)
        except NoteUploadFailException:
            if not self.rules.skip_failed:
                raise
            # All notes failed to upload due to notebook root creation failure
            notebook_stats.failed += parse_stats.successful
            return notebook_stats

        # Phase 4: Upload successfully parsed notes
        successful_results = [r for r in parse_stats.results if not r.failed and r.note]
        skipped_results = []  # Track skipped notes for export

        for idx, result in enumerate(successful_results, 1):
            note = result.note
            upload_result, skip_reason = self._upload_single_note(note, idx, parse_stats.total, notebook_name, result)

            if upload_result == "success":
                notebook_stats.successful += 1
            elif upload_result == "skipped":
                notebook_stats.skipped += 1
                # Add skip reason to result for export
                result.skip_reason = skip_reason
                skipped_results.append(result)
            elif upload_result == "failed":
                notebook_stats.failed += 1

        # Phase 5: Export skipped notes to unimported directory
        if skipped_results:
            if not notebook_stats.failed_directory:
                # Create unimported directory if not already created
                notebook_stats.failed_directory = export_all_failed_notes(
                    skipped_results, notebook_name, self.failed_export_dir, "skipped"
                )
            else:
                # Use existing directory
                for result in skipped_results:
                    try:
                        from enex2notion.failed_note_exporter import export_failed_note

                        export_failed_note(result, notebook_stats.failed_directory, notebook_name, "skipped")
                    except Exception as e:
                        logger.error(f"Failed to export skipped note: {e}")

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
        note_blocks = self._parse_note(note)
        if not note_blocks:
            skip_reason = "No blocks after parsing (empty or unsupported content)"
            logger.warning(f"Skipping note '{note.title}': {skip_reason}")
            return "skipped", skip_reason

        # Upload to Notion
        logger.info(f"Uploading note {note_idx}/{total_notes}: '{note.title}'")

        try:
            self._upload_note(self.notebook_root, note, note_blocks)
            self.done_hashes.add(note.note_hash)
            return "success", None
        except NoteUploadFailException as e:
            logger.error(f"Failed to upload note '{note.title}': {e}")
            if not self.rules.skip_failed:
                raise

            # Export failed upload to ENEX
            try:
                from enex2notion.failed_note_exporter import export_failed_note, create_failed_directory

                unimported_dir = create_failed_directory(notebook_name, self.failed_export_dir)
                export_failed_note(parse_result, unimported_dir, notebook_name, "failed")
            except Exception as export_err:
                logger.error(f"Failed to export failed note: {export_err}")

            return "failed", None

    def _parse_note(self, note):
        try:
            return parse_note(note, self.rules)
        except Exception as e:
            logger.error(f"Failed to parse note '{note.title}'")
            logger.debug(e, exc_info=e)
            return []

    def _get_notebook_root(self, notebook_title):
        if self.import_root is None:
            return None

        error_message = f"Failed to get notebook root for '{notebook_title}'"
        get_func = get_notebook_database if self.mode == "DB" else get_notebook_page

        return self._attempt_upload(
            get_func, error_message, self.import_root, notebook_title
        )

    def _upload_note(self, notebook_root, note, note_blocks):
        self._attempt_upload(
            upload_note,
            f"Failed to upload note '{note.title}' to Notion",
            notebook_root,
            note,
            note_blocks,
            self.rules.keep_failed,
        )

    def _attempt_upload(self, upload_func, error_message, *args, **kwargs):
        for attempt in itertools.count(1):
            try:
                return upload_func(*args, **kwargs)
            except NoteUploadFailException as e:
                logger.debug(f"Upload error: {e}", exc_info=e)

                if attempt == self.rules.retry:
                    logger.error(f"{error_message}!")
                    raise

                logger.warning(f"{error_message}! Retrying...")
