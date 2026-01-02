"""Convert internal block representations to official Notion API format."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def convert_block_to_api_format(notion_block) -> dict[str, Any] | None:
    """Convert internal block to official API format.

    Args:
        notion_block: Internal NotionBaseBlock instance

    Returns:
        Block dict in official API format, or None if unsupported
    """
    # Get the class name of the internal block
    class_name = notion_block.__class__.__name__
    
    # Map class names to conversion functions
    converters = {
        "NotionTextBlock": _convert_text_block,
        "NotionHeaderBlock": lambda b: _convert_heading(b, 1),
        "NotionSubheaderBlock": lambda b: _convert_heading(b, 2),
        "NotionSubsubheaderBlock": lambda b: _convert_heading(b, 3),
        "NotionBulletedListBlock": lambda b: _convert_list_item(b, "bulleted_list_item"),
        "NotionNumberedListBlock": lambda b: _convert_list_item(b, "numbered_list_item"),
        "NotionTodoBlock": _convert_todo,
        "NotionDividerBlock": _convert_divider,
        "NotionBookmarkBlock": _convert_bookmark,
        "NotionCodeBlock": _convert_code,
        "NotionQuoteBlock": _convert_quote,
        "NotionCalloutBlock": _convert_callout,
        "NotionImageBlock": _convert_image,
    }

    converter = converters.get(class_name)
    
    if converter:
        try:
            return converter(notion_block)
        except Exception as e:
            logger.error(f"Failed to convert block type {class_name}: {e}")
            return None
    
    logger.warning(f"Unsupported block type: {class_name}")
    return None


def _convert_text_block(notion_block) -> dict[str, Any]:
    """Convert text block to paragraph."""
    text_prop = getattr(notion_block, "text_prop", None)
    
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "color": "default"
        }
    }


def _convert_heading(notion_block, level: int) -> dict[str, Any]:
    """Convert heading block."""
    text_prop = getattr(notion_block, "text_prop", None)
    
    heading_type = f"heading_{level}"
    return {
        "type": heading_type,
        heading_type: {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "color": "default"
        }
    }


def _convert_list_item(notion_block, list_type: str) -> dict[str, Any]:
    """Convert list item block."""
    text_prop = getattr(notion_block, "text_prop", None)
    
    return {
        "type": list_type,
        list_type: {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "color": "default"
        }
    }


def _convert_todo(notion_block) -> dict[str, Any]:
    """Convert todo block."""
    text_prop = getattr(notion_block, "text_prop", None)
    checked = notion_block.attrs.get("checked", False)
    
    return {
        "type": "to_do",
        "to_do": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "checked": checked,
            "color": "default"
        }
    }


def _convert_divider(notion_block) -> dict[str, Any]:
    """Convert divider block."""
    return {"type": "divider", "divider": {}}


def _convert_bookmark(notion_block) -> dict[str, Any]:
    """Convert bookmark block."""
    url = notion_block.attrs.get("link", "")
    
    return {
        "type": "bookmark",
        "bookmark": {
            "url": url
        }
    }


def _convert_code(notion_block) -> dict[str, Any]:
    """Convert code block."""
    text_prop = getattr(notion_block, "text_prop", None)
    language = notion_block.attrs.get("language", "plain text")
    
    return {
        "type": "code",
        "code": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "language": language
        }
    }


def _convert_quote(notion_block) -> dict[str, Any]:
    """Convert quote block."""
    text_prop = getattr(notion_block, "text_prop", None)
    
    return {
        "type": "quote",
        "quote": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "color": "default"
        }
    }


def _convert_callout(notion_block) -> dict[str, Any]:
    """Convert callout block."""
    text_prop = getattr(notion_block, "text_prop", None)
    icon = notion_block.attrs.get("icon", "💡")
    
    return {
        "type": "callout",
        "callout": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "icon": {"type": "emoji", "emoji": icon},
            "color": "default"
        }
    }


def _convert_image(notion_block) -> dict[str, Any]:
    """Convert image block.
    
    Uses file_upload type with the uploaded file ID.
    """
    # Get file_upload ID from block attributes
    # The ID should be set by uploading the image before conversion
    file_upload_id = notion_block.attrs.get("file_upload_id")
    
    if not file_upload_id:
        logger.warning("Image block has no file_upload_id - skipping")
        return None
    
    return {
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {
                "id": file_upload_id
            }
        }
    }


def _convert_text_prop(text_prop) -> list[dict[str, Any]]:
    """Convert text property to rich_text array.

    Args:
        text_prop: TextProp object with text and properties

    Returns:
        List of rich_text objects
    """
    if not text_prop or not hasattr(text_prop, "properties"):
        return []
    
    # Properties format from notion-py:
    # [[text]] or [[text, [["formatting"]]]]
    # formatting can be: ["b"], ["i"], ["s"], ["c"], ["a", "url"], ["h", "color"]
    
    rich_text_items = []
    
    for prop in text_prop.properties:
        if not prop or not prop[0]:
            continue
            
        text_content = prop[0]
        # Notion API limit: 2000 chars per rich_text object
        if len(text_content) > 2000:
            # Split long text into multiple rich_text objects
            for i in range(0, len(text_content), 2000):
                chunk = text_content[i:i+2000]
                rich_text_items.append(_create_rich_text_object(chunk, prop[1:]))
        else:
            rich_text_items.append(_create_rich_text_object(text_content, prop[1:]))
    
    return rich_text_items if rich_text_items else [{"type": "text", "text": {"content": ""}}]


def _create_rich_text_object(text: str, formatting: list) -> dict[str, Any]:
    """Create a single rich_text object with formatting.

    Args:
        text: Text content
        formatting: List of formatting instructions

    Returns:
        Rich text object dict
    """
    annotations = {
        "bold": False,
        "italic": False,
        "strikethrough": False,
        "underline": False,
        "code": False,
        "color": "default"
    }
    
    link_url = None
    
    # Parse formatting
    if formatting:
        for fmt in formatting[0] if formatting else []:
            if not isinstance(fmt, list):
                continue
                
            if not fmt:
                continue
                
            fmt_type = fmt[0]
            
            if fmt_type == "b":
                annotations["bold"] = True
            elif fmt_type == "i":
                annotations["italic"] = True
            elif fmt_type == "s":
                annotations["strikethrough"] = True
            elif fmt_type == "_":
                annotations["underline"] = True
            elif fmt_type == "c":
                annotations["code"] = True
            elif fmt_type == "a" and len(fmt) > 1:
                # Store URL for validation before adding to rich_text
                link_url = fmt[1]
            elif fmt_type == "h" and len(fmt) > 1:
                # Color mapping from notion-py to official API
                color_map = {
                    "gray": "gray",
                    "brown": "brown",
                    "orange": "orange",
                    "yellow": "yellow",
                    "green": "green",
                    "blue": "blue",
                    "purple": "purple",
                    "pink": "pink",
                    "red": "red",
                    "gray_background": "gray_background",
                    "brown_background": "brown_background",
                    "orange_background": "orange_background",
                    "yellow_background": "yellow_background",
                    "green_background": "green_background",
                    "blue_background": "blue_background",
                    "purple_background": "purple_background",
                    "pink_background": "pink_background",
                    "red_background": "red_background",
                }
                annotations["color"] = color_map.get(fmt[1], "default")
    
    rich_text_obj = {
        "type": "text",
        "text": {"content": text},
        "annotations": annotations
    }
    
    # Validate URL before adding as link
    # Notion API rejects certain URL schemes (evernote://, malformed mongodb://, etc.)
    if link_url and _is_notion_compatible_url(link_url):
        # Valid URL - add as clickable link
        rich_text_obj["text"]["link"] = {"url": link_url}
    elif link_url:
        # Invalid URL for Notion - try markdown format as workaround
        if link_url.lower().startswith("evernote://"):
            # Try markdown format [text](url) in case Notion parses it differently
            # If this doesn't work, it will just appear as plain markdown text
            rich_text_obj["text"]["content"] = f"[{text}]({link_url})"
            logger.debug(f"Trying markdown format for evernote:// link")
        else:
            # For other invalid URLs, keep text only
            logger.debug(f"Removed unsupported URL (keeping text): {link_url[:100]}")
    
    return rich_text_obj


def _is_notion_compatible_url(url: str) -> bool:
    """Check if URL scheme is compatible with Notion API.
    
    Notion API rejects certain URL schemes:
    - evernote:// (internal Evernote links)
    - Malformed URLs (incomplete connection strings, etc.)
    
    Args:
        url: URL string to validate
        
    Returns:
        True if Notion will accept this URL as a link
    """
    if not url or not isinstance(url, str):
        return False
    
    url = url.strip()
    
    if not url:
        return False
    
    # Reject Evernote internal links - Notion's public API does not support this scheme
    # (even though Notion's own import creates these using internal APIs)
    if url.lower().startswith("evernote://"):
        return False
    
    # Must have a colon to be a URL
    if ":" not in url:
        return False
    
    scheme = url.split(":", 1)[0].lower()
    remainder = url.split(":", 1)[1]
    
    # Common valid schemes that Notion's public API supports
    valid_schemes = {"http", "https", "ftp", "ftps", "mailto", "tel"}
    
    if scheme in valid_schemes:
        # Basic check - should have something after the colon
        return len(remainder) > 0
    
    # For other schemes (mongodb, ssh, etc.), check if properly formed
    # Reject obviously broken URLs like "mongodb://llocker" (no domain)
    if scheme and remainder.startswith("//"):
        # Check if there's actually a host/domain after //
        host_part = remainder[2:].split("/")[0].split("?")[0]
        # Must have at least a domain-like structure
        # Reject single words without dots (unless localhost)
        if host_part and (":" in host_part or "." in host_part or host_part == "localhost"):
            return True
        return False
    
    # For non-// schemes (like mailto:), accept if there's content
    return len(remainder) > 0
