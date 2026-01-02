"""Handle image uploads to Notion.

Converts Evernote resources (base64 or source URLs) into Notion file upload IDs
using the official Direct Upload API.
"""
import logging
import mimetypes
from typing import Optional

logger = logging.getLogger(__name__)


def upload_image_to_notion(resource, notion_api) -> Optional[str]:
    """Upload image resource to Notion using Direct Upload API.

    Args:
        resource: EvernoteResource object with data_bin (bytes)
        notion_api: NotionAPIWrapper instance

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

    # Determine filename from hash and MIME type
    mime = getattr(resource, "mime", "image/png")
    file_ext = mimetypes.guess_extension(mime) or ".png"
    
    # Use md5 or hash for filename
    resource_hash = getattr(resource, "md5", getattr(resource, "hash", "unknown"))
    filename = f"{resource_hash}{file_ext}"

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
        logger.error(f"Failed to upload image {filename}: {e}")
        return None


