import logging
import re
from datetime import datetime
from pathlib import Path

from enex2notion.enex_types import NoteParseResult

logger = logging.getLogger(__name__)

ENEX_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export3.dtd">
<en-export export-date="{export_date}" application="enex2notion" version="1.0.0">
"""

ENEX_FOOTER = "</en-export>\n"


def create_failed_directory(notebook_name: str, base_path: Path) -> Path:
    """Create a directory for unimported notes with notebook name and timestamp.

    Format: <notebook_name>_YYYYMMDD_HHMMSS_unimported/

    Args:
        notebook_name: Name of the notebook (from ENEX filename)
        base_path: Base directory where unimported directory should be created

    Returns:
        Path to the created unimported directory
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_name = sanitize_filename(notebook_name)
    unimported_dir = base_path / f"{sanitized_name}_{timestamp}_unimported"
    unimported_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Created unimported notes directory: {unimported_dir}")
    return unimported_dir


def sanitize_filename(filename: str) -> str:
    """Sanitize a string to be safe for use as a filename.

    Removes or replaces characters that are problematic in filenames.

    Args:
        filename: String to sanitize

    Returns:
        Sanitized filename string
    """
    # Replace common problematic characters
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)

    # Replace multiple spaces/underscores with single underscore
    sanitized = re.sub(r"[_\s]+", "_", sanitized)

    # Remove leading/trailing underscores and spaces
    sanitized = sanitized.strip("_ ")

    # Limit length (leave room for timestamp and _failed.enex)
    max_length = 100
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # Ensure not empty
    if not sanitized:
        sanitized = "untitled"

    return sanitized


def export_failed_note(
    result: NoteParseResult, unimported_dir: Path, notebook_name: str, suffix: str = "failed"
) -> Path:
    """Export a failed/skipped note to an ENEX file.

    Creates a valid ENEX file containing the single note for debugging.
    If result has a skip_reason, it will be injected as a comment in the note content.

    Args:
        result: NoteParseResult containing the note and raw XML
        unimported_dir: Directory to save the unimported note
        notebook_name: Name of the source notebook for logging
        suffix: Suffix for filename (e.g., 'failed', 'skipped')

    Returns:
        Path to the created ENEX file
    """
    # Determine note title for filename
    note_title = _extract_note_title(result)
    sanitized_title = sanitize_filename(note_title)

    # Create filename
    filename = f"{sanitized_title}_{suffix}.enex"
    file_path = unimported_dir / filename

    # Handle duplicate filenames
    counter = 1
    while file_path.exists():
        filename = f"{sanitized_title}_{suffix}_{counter}.enex"
        file_path = unimported_dir / filename
        counter += 1

    # Generate ENEX content with optional skip reason injection
    export_date = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    enex_content = ENEX_HEADER.format(export_date=export_date)
    
    # Inject skip reason into note content if available
    if result.skip_reason and suffix == "skipped":
        modified_xml = _inject_skip_reason(result.raw_xml, result.skip_reason)
        enex_content += modified_xml
    else:
        enex_content += result.raw_xml
    
    enex_content += ENEX_FOOTER

    # Write to file
    file_path.write_text(enex_content, encoding="utf-8")

    logger.debug(f"Exported {suffix} note '{note_title}' from '{notebook_name}' to {file_path.name}")

    return file_path


def export_all_failed_notes(
    failed_results: list[NoteParseResult], notebook_name: str, base_path: Path, suffix: str = "failed"
) -> Path | None:
    """Export all failed/skipped notes from a notebook to a dedicated directory.

    Args:
        failed_results: List of failed NoteParseResult objects
        notebook_name: Name of the notebook
        base_path: Base directory for unimported exports
        suffix: Suffix for filenames (e.g., 'failed', 'skipped')

    Returns:
        Path to unimported directory if any notes were exported, None otherwise
    """
    if not failed_results:
        return None

    unimported_dir = create_failed_directory(notebook_name, base_path)

    exported_count = 0
    for result in failed_results:
        try:
            export_failed_note(result, unimported_dir, notebook_name, suffix)
            exported_count += 1
        except Exception as e:
            logger.error(f"Failed to export {suffix} note: {e}")
            logger.debug("Export error details", exc_info=e)

    logger.info(f"Exported {exported_count}/{len(failed_results)} {suffix} notes to {unimported_dir.name}")

    return unimported_dir


def _inject_skip_reason(raw_xml: str, skip_reason: str) -> str:
    """Inject skip reason as an XML comment at the beginning of note content.

    Args:
        raw_xml: Original note XML
        skip_reason: Reason the note was skipped

    Returns:
        Modified XML with skip reason comment
    """
    try:
        import xml.etree.ElementTree as ET

        # Parse the XML
        root = ET.fromstring(raw_xml)

        # Create comment with skip reason
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        comment_text = f"\n=== SKIPPED BY ENEX2NOTION ===\nTimestamp: {timestamp}\nReason: {skip_reason}\n=== END SKIP REASON ===\n"

        # Find content element
        content_elem = root.find("content")
        if content_elem is not None and content_elem.text:
            # Inject comment at the beginning of content
            original_content = content_elem.text
            # Add as HTML comment within the ENML content
            content_elem.text = f"<!-- {comment_text} -->{original_content}"

        # Convert back to string
        return ET.tostring(root, encoding="unicode")
    except Exception as e:
        # If injection fails, return original XML
        logger.debug(f"Failed to inject skip reason: {e}")
        return raw_xml


def _extract_note_title(result: NoteParseResult) -> str:
    """Extract note title from parse result for filename.

    Tries to get title from parsed note first, then from raw XML if parsing failed.

    Args:
        result: NoteParseResult containing note data

    Returns:
        Note title string or 'untitled' if not found
    """
    # Try parsed note first
    if result.note and result.note.title:
        return result.note.title

    # Try to extract from raw XML
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(result.raw_xml)
        title_elem = root.find(".//title")
        if title_elem is not None and title_elem.text:
            return title_elem.text
    except Exception:
        pass

    # Fallback
    return "untitled"
