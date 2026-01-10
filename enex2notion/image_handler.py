"""Handle image uploads to Notion.

Converts Evernote resources (base64 or source URLs) into Notion file upload IDs
using the official Direct Upload API.
"""
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

from enex2notion.parse_warnings import add_warning

logger = logging.getLogger(__name__)

# NOTE: We now allow all file types to be uploaded.
# Notion's API will handle any unsupported types and return appropriate errors.
# The multi-part upload system supports files of any size and type.


def upload_image_to_notion(
    resource, 
    notion_api, 
    rejected_tracker=None, 
    notebook_name="", 
    note_title="",
    unsupported_dir: Optional[Path] = None
) -> Optional[str]:
    """Upload image resource to Notion using Direct Upload API.

    Args:
        resource: EvernoteResource object with data_bin (bytes)
        notion_api: NotionAPIWrapper instance
        rejected_tracker: RejectedFilesTracker instance (optional)
        notebook_name: Name of notebook for tracking rejected files
        note_title: Title of note for tracking rejected files
        unsupported_dir: Directory to save unsupported files (optional)

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

    # Retry logic with exponential backoff for transient errors only
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
            error_msg = str(e)
            
            # Check if Notion rejected the file extension - don't retry, save to disk instead
            if "extension that is not supported" in error_msg:
                logger.info(f"Notion doesn't support {filename} extension - will save to disk")
                break
            
            # Check if it's a transient error worth retrying
            error_msg_lower = error_msg.lower()
            is_transient = any(x in error_msg_lower for x in [
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
            # Don't treat as warning/error - save to disk with original filename
            # Save file to disk with original filename (not modified)
            if unsupported_dir:
                saved_path = _save_unsupported_file(
                    data_bin=data_bin,
                    filename=filename,
                    notebook_name=notebook_name,
                    note_title=note_title,
                    unsupported_dir=unsupported_dir,
                )
                # Store the download location for the placeholder block
                if saved_path and hasattr(resource, 'attrs'):
                    resource.attrs = getattr(resource, 'attrs', {})
                    resource.attrs['download_location'] = str(saved_path)
                    # Log with file location immediately after saving
                    logger.info(
                        f"File {filename} cannot be uploaded to Notion (unsupported type). "
                        f"Saved to: {saved_path}"
                    )
            
            # Note: Don't track in rejected_tracker - unsupported files are handled separately
        else:
            # Generic upload failure - add warning and track
            reason = f"Upload failed: {str(last_exception)[:100]}"
            add_warning(f"File upload failed: '{filename}' - {str(last_exception)[:80]}")
            logger.error(f"Failed to upload file {filename}: {last_exception}")
            
            # Save file to disk if directory provided
            if unsupported_dir:
                _save_unsupported_file(
                    data_bin=data_bin,
                    filename=filename,
                    notebook_name=notebook_name,
                    note_title=note_title,
                    unsupported_dir=unsupported_dir,
                )
            
            if rejected_tracker:
                rejected_tracker.add_rejected_file(
                    notebook_name=notebook_name,
                    note_title=note_title,
                    filename=filename,
                    reason=reason,
                    file_extension=file_ext,
                )
    
    return None


def _save_unsupported_file(
    data_bin: bytes,
    filename: str,
    notebook_name: str,
    note_title: str,
    unsupported_dir: Path,
) -> Optional[Path]:
    """Save an unsupported file to disk for manual upload.
    
    Args:
        data_bin: File binary data
        filename: Original filename
        notebook_name: Notebook name for organizing files
        note_title: Note title for organizing files
        unsupported_dir: Base directory for unsupported files
    
    Returns:
        Path to saved file, or None if save failed
    """
    try:
        # Create directory structure: unsupported_dir/notebook/note/
        # Sanitize names for filesystem
        safe_notebook = _sanitize_filename(notebook_name) or "Unknown_Notebook"
        safe_note = _sanitize_filename(note_title) or "Untitled_Note"
        
        file_dir = unsupported_dir / safe_notebook / safe_note
        file_dir.mkdir(parents=True, exist_ok=True)
        
        # Save file
        file_path = file_dir / filename
        
        # Handle filename collisions
        if file_path.exists():
            base = file_path.stem
            ext = file_path.suffix
            counter = 1
            while file_path.exists():
                file_path = file_dir / f"{base}_{counter}{ext}"
                counter += 1
        
        file_path.write_bytes(data_bin)
        # Don't log here - logging happens in caller with full context
        return file_path
        
    except Exception as e:
        logger.error(f"Failed to save unsupported file {filename} to disk: {e}")
        return None


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a directory/file name.
    
    Args:
        name: String to sanitize
        
    Returns:
        Sanitized string safe for filesystem use
    """
    if not name:
        return ""
    
    # Replace invalid characters with underscore
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")
    
    # Remove leading/trailing spaces and dots
    name = name.strip(" .")
    
    # Limit length to 200 characters
    if len(name) > 200:
        name = name[:200]
    
    return name


