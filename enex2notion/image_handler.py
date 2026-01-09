"""Handle image uploads to Notion.

Converts Evernote resources (base64 or source URLs) into Notion file upload IDs
using the official Direct Upload API.
"""
import logging
import mimetypes
import os
import time
from typing import Optional

from enex2notion.parse_warnings import add_warning

logger = logging.getLogger(__name__)

# NOTE: We now allow all file types to be uploaded.
# Notion's API will handle any unsupported types and return appropriate errors.
# The multi-part upload system supports files of any size and type.


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
    mime = getattr(resource, "mime", "application/octet-stream")
    
    if not filename:
        # Fall back to hash-based filename if original name not available
        file_ext = mimetypes.guess_extension(mime) or ".bin"
        resource_hash = getattr(resource, "md5", getattr(resource, "hash", "unknown"))
        filename = f"{resource_hash}{file_ext}"

    file_ext = os.path.splitext(filename)[1].lower()
    logger.debug(f"Uploading {filename} ({len(data_bin)} bytes, {mime})")

    # Retry logic with exponential backoff for transient errors
    max_retries = 3
    last_exception = None
    
    for attempt in range(max_retries):
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
            last_exception = e
            is_last_attempt = (attempt == max_retries - 1)
            
            # Check if it's a transient error worth retrying
            error_msg = str(e).lower()
            is_transient = any(x in error_msg for x in [
                "timeout", "connection", "rate limit", "429", "503", "502", "500"
            ])
            
            if is_transient and not is_last_attempt:
                wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                logger.warning(f"Transient error uploading {filename} (attempt {attempt + 1}/{max_retries}): {e}")
                logger.debug(f"  Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            
            # Last attempt or permanent error - break and handle below
            break
    
    # If we get here, all retries failed - handle the last exception
    if last_exception:
        error_msg = str(last_exception)
        # Check if it's an unsupported extension/type error from Notion API
        if "extension that is not supported" in error_msg or "not supported" in error_msg.lower():
            reason = f"Notion API rejection: {error_msg[:150]}"
            add_warning(
                f"File not uploaded: '{filename}' - Notion does not support this file type. "
                f"File will be stored as generic attachment."
            )
            logger.warning(
                f"Notion rejected {filename} ({file_ext}): {error_msg}. "
                f"Note: File was attempted for upload but Notion's API may not preview this type in the UI. "
                f"It will be stored as a downloadable attachment if supported."
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
            # Generic upload failure - add warning and track
            reason = f"Upload failed: {str(last_exception)[:100]}"
            add_warning(f"File upload failed: '{filename}' - {str(last_exception)[:80]}")
            logger.error(f"Failed to upload file {filename}: {last_exception}")
            if rejected_tracker:
                rejected_tracker.add_rejected_file(
                    notebook_name=notebook_name,
                    note_title=note_title,
                    filename=filename,
                    reason=reason,
                    file_extension=file_ext,
                )
    
    return None


