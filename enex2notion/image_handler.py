"""Handle image uploads to Notion.

Converts Evernote resources (base64 or source URLs) into Notion file upload IDs
using the official Direct Upload API.
"""
import logging
import mimetypes
import os
from typing import Optional

from enex2notion.parse_warnings import add_warning

logger = logging.getLogger(__name__)

# Known unsupported file extensions by Notion's File Upload API
# This list is not exhaustive - Notion may reject other extensions
UNSUPPORTED_EXTENSIONS = {
    # Apple formats
    ".pages", ".numbers", ".key",
    # Executable formats (already banned by enex_parser for security)
    ".exe", ".app", ".apk", ".jar", ".js",
    # Add more as discovered
}


def upload_image_to_notion(resource, notion_api, rejected_tracker=None, notebook_name="", note_title="") -> Optional[str]:
    """Upload image resource to Notion using Direct Upload API.

    Args:
        resource: EvernoteResource object with data_bin (bytes)
        notion_api: NotionAPIWrapper instance
        rejected_tracker: RejectedFilesTracker instance (optional)
        notebook_name: Name of notebook for tracking rejected files
        note_title: Title of note for tracking rejected files

    Returns:
        File upload ID string (to be used with type: file_upload), or None if failed
    """
    if not resource:
        return None

    # Check if we have image data
    data_bin = getattr(resource, "data_bin", None)
    if not data_bin:
        logger.warning(f"Resource has no data_bin, cannot upload")
        return None

    # Get original filename from resource, or fall back to hash
    filename = getattr(resource, "file_name", None)
    mime = getattr(resource, "mime", "image/png")
    
    if not filename:
        # Fall back to hash-based filename if original name not available
        file_ext = mimetypes.guess_extension(mime) or ".bin"
        resource_hash = getattr(resource, "md5", getattr(resource, "hash", "unknown"))
        filename = f"{resource_hash}{file_ext}"

    # Pre-flight check: Detect known unsupported extensions
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext in UNSUPPORTED_EXTENSIONS:
        reason = f"Unsupported extension '{file_ext}' - not accepted by Notion File Upload API"
        add_warning(f"Attached file not uploaded: '{filename}' ({file_ext} format not supported by Notion)")
        logger.warning(
            f"Skipping {filename}: '{file_ext}' files are not supported by Notion's File Upload API. "
            f"Consider converting to PDF, DOCX, or another supported format."
        )
        # Track rejected file
        if rejected_tracker:
            rejected_tracker.add_rejected_file(
                notebook_name=notebook_name,
                note_title=note_title,
                filename=filename,
                reason=reason,
                file_extension=file_ext,
            )
        return None
    
    logger.debug(f"Uploading {filename} ({len(data_bin)} bytes, {mime})")

    try:
        # Upload to Notion using Direct Upload API
        upload_id = notion_api.upload_file(
            file_data=data_bin,
            filename=filename,
            mime_type=mime,
        )
        logger.debug(f"  Uploaded as file_upload ID: {upload_id}")
        return upload_id
    except Exception as e:
        error_msg = str(e)
        # Check if it's an unsupported extension error from Notion API
        if "extension that is not supported" in error_msg:
            reason = f"Rejected by Notion API: Extension '{file_ext}' not supported"
            add_warning(f"Attached file not uploaded: '{filename}' (Notion rejected {file_ext} format)")
            logger.warning(
                f"Skipping {filename}: File extension '{file_ext}' not supported by Notion. "
                f"Consider converting to a supported format."
            )
            # Track rejected file
            if rejected_tracker:
                rejected_tracker.add_rejected_file(
                    notebook_name=notebook_name,
                    note_title=note_title,
                    filename=filename,
                    reason=reason,
                    file_extension=file_ext,
                )
        else:
            logger.error(f"Failed to upload image {filename}: {e}")
        return None


