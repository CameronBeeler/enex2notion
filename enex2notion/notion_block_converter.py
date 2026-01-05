"""Convert internal block representations to official Notion API format."""
import logging
import re
from typing import Any

from enex2notion.parse_warnings import add_warning

logger = logging.getLogger(__name__)


def _is_valid_url(url: str) -> bool:
    """Check if URL is valid for Notion API.
    
    Args:
        url: URL string to validate
        
    Returns:
        True if URL is valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # Must start with http:// or https://
    if not url.startswith(("http://", "https://")):
        return False
    
    # Basic URL structure validation
    # Allow any characters after protocol - Notion will do final validation
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'[^\s/$.?#]+'  # domain (at least one char, no spaces/special)
        r'.*$',  # anything else
        re.IGNORECASE
    )
    
    return bool(url_pattern.match(url))


def _get_resource_filename(notion_block) -> str:
    """Extract filename from a resource block.
    
    Args:
        notion_block: NotionUploadableBlock with a resource attribute
        
    Returns:
        Filename string, or "unknown file" if not found
    """
    if hasattr(notion_block, "resource") and notion_block.resource:
        resource = notion_block.resource
        if hasattr(resource, "file_name") and resource.file_name:
            return resource.file_name
        # Fall back to hash if available
        if hasattr(resource, "md5") and resource.md5:
            return f"{resource.md5} (unnamed)"
    return "unknown file"


def _create_failed_upload_placeholder(filename: str, file_type: str) -> dict[str, Any]:
    """Create a visible placeholder for a failed file upload.
    
    Args:
        filename: Name of the file that failed to upload
        file_type: Type of file ("image", "PDF", "file")
        
    Returns:
        Callout block dict in API format
    """
    return {
        "type": "callout",
        "callout": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": f"⚠️ {file_type.capitalize()} attachment failed to import: \"{filename}\""
                    },
                    "annotations": {
                        "bold": False,
                        "italic": True,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "default"
                    }
                }
            ],
            "icon": {"type": "emoji", "emoji": "⚠️"},
            "color": "yellow_background"
        }
    }


def _create_inline_warning_marker(message: str) -> dict[str, Any]:
    """Create a compact inline warning paragraph for format transformations.
    
    Args:
        message: Warning message to display
        
    Returns:
        Paragraph block dict in API format
    """
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": "⚠️ "},
                    "annotations": {
                        "bold": False,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "orange"
                    }
                },
                {
                    "type": "text",
                    "text": {"content": message},
                    "annotations": {
                        "bold": False,
                        "italic": True,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "gray"
                    }
                }
            ],
            "color": "default"
        }
    }


def convert_block_to_api_format(notion_block) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Convert internal block to official API format.

    Args:
        notion_block: Internal NotionBaseBlock instance

    Returns:
        Block dict in official API format, list of blocks (for split tables), or None if unsupported
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
        "NotionPDFBlock": _convert_pdf,
        "NotionFileBlock": _convert_file,
        "NotionTableBlock": _convert_table,
    }

    converter = converters.get(class_name)
    
    if converter:
        try:
            result = converter(notion_block)
            # Tables may return a list if they were split
            return result
        except Exception as e:
            logger.error(f"Failed to convert block type {class_name}: {e}")
            return None
    
    logger.warning(f"Unsupported block type: {class_name}")
    return None


def _convert_text_block(notion_block) -> dict[str, Any] | list[dict[str, Any]]:
    """Convert text block to paragraph.
    
    Returns:
        Single paragraph or list of paragraphs if content exceeds 100 rich_text items
    """
    text_prop = getattr(notion_block, "text_prop", None)
    rich_text, overflow = _convert_text_prop_with_overflow(text_prop) if text_prop else ([], [])
    
    blocks = [{
        "type": "paragraph",
        "paragraph": {
            "rich_text": rich_text if rich_text else [],
            "color": "default"
        }
    }]
    
    # Add overflow as additional paragraphs with warning marker
    if overflow:
        # Insert warning marker before continuation blocks
        blocks.append(_create_inline_warning_marker(
            f"Paragraph split into {len(overflow) + 1} blocks (API limit: 100 formatting segments per block)"
        ))
    
    for overflow_chunk in overflow:
        blocks.append({
            "type": "paragraph",
            "paragraph": {
                "rich_text": overflow_chunk,
                "color": "default"
            }
        })
    
    return blocks if len(blocks) > 1 else blocks[0]


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
    url = notion_block.attrs.get("link", "").strip()
    
    # Validate URL - Notion requires proper HTTP/HTTPS URLs
    if not url:
        logger.warning("Bookmark block has empty URL - converting to paragraph")
        add_warning("Empty bookmark removed")
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "⚠️ [Empty bookmark]"}}],
                "color": "default"
            }
        }
    
    # Ensure URL has proper protocol
    if not url.startswith(("http://", "https://")):
        logger.warning(f"Bookmark URL missing protocol: {url} - adding https://")
        url = f"https://{url}"
    
    # Basic validation - check if URL has valid structure
    if not _is_valid_url(url):
        logger.warning(f"Invalid bookmark URL: {url} - converting to paragraph")
        add_warning(f"Invalid bookmark URL: {url[:80]}")
        return {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": f"⚠️ [Invalid link: {url}]"}}],
                "color": "default"
            }
        }
    
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
    If upload failed, creates a visible placeholder.
    """
    # Get file_upload ID from block attributes
    # The ID should be set by uploading the image before conversion
    file_upload_id = notion_block.attrs.get("file_upload_id")
    upload_failed = notion_block.attrs.get("upload_failed", False)
    
    if not file_upload_id:
        if upload_failed:
            # Create a visible placeholder paragraph
            filename = _get_resource_filename(notion_block)
            return _create_failed_upload_placeholder(filename, "image")
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


def _convert_text_prop_with_overflow(text_prop) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Convert text property to rich_text array with overflow handling.

    Args:
        text_prop: TextProp object with text and properties

    Returns:
        Tuple of (main_rich_text, overflow_chunks)
        - main_rich_text: First 100 items
        - overflow_chunks: List of 100-item chunks for additional blocks
    """
    if not text_prop or not hasattr(text_prop, "properties"):
        return [], []
    
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
    
    if not rich_text_items:
        return [{"type": "text", "text": {"content": ""}}], []
    
    # Notion API limit: max 100 rich_text items per block
    if len(rich_text_items) > 100:
        add_warning(
            f"Paragraph split into {(len(rich_text_items) + 99) // 100} continuation blocks "
            f"(API limit: 100 formatting segments per block, had {len(rich_text_items)})"
        )
        logger.warning(
            f"Rich text array has {len(rich_text_items)} items, splitting into continuation blocks. "
            "Content will be preserved."
        )
        main_text = rich_text_items[:100]
        overflow_chunks = [
            rich_text_items[i:i+100]
            for i in range(100, len(rich_text_items), 100)
        ]
        return main_text, overflow_chunks
    
    return rich_text_items, []


def _convert_text_prop(text_prop, max_items: int = 100) -> list[dict[str, Any]]:
    """Convert text property to rich_text array (legacy, truncates overflow).

    Args:
        text_prop: TextProp object with text and properties
        max_items: Maximum number of rich_text items (Notion API limit is 100)

    Returns:
        List of rich_text objects (limited to max_items)
    """
    main_text, _ = _convert_text_prop_with_overflow(text_prop)
    return main_text[:max_items] if main_text else [{"type": "text", "text": {"content": ""}}]


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
    if link_url:
        if _is_notion_compatible_url(link_url):
            # Valid URL - add as clickable link
            logger.debug(f"Adding link to text: {link_url}")
            rich_text_obj["text"]["link"] = {"url": link_url}
        else:
            # Invalid URL for Notion - try markdown format as workaround
            if link_url.lower().startswith("evernote://"):
                # Try markdown format [text](url) in case Notion parses it differently
                # If this doesn't work, it will just appear as plain markdown text
                rich_text_obj["text"]["content"] = f"⚠️ [{text}]({link_url})"
                add_warning(f"Evernote internal link converted to text: {link_url[:80]}")
                logger.debug(f"Converted evernote:// link to markdown: {link_url}")
            else:
                # For other invalid URLs, keep text with warning marker
                rich_text_obj["text"]["content"] = f"⚠️ {text}"
                add_warning(f"Unsupported URL scheme removed: {link_url[:80]}")
                logger.warning(f"Removed unsupported URL (keeping text): {link_url[:100]}")
    
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
    
    # Reject special/internal schemes that Notion's public API doesn't support
    invalid_schemes = {"evernote", "about", "file", "javascript", "data", "vbscript"}
    for invalid_scheme in invalid_schemes:
        if url.lower().startswith(f"{invalid_scheme}:"):
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


def _convert_pdf(notion_block) -> dict[str, Any]:
    """Convert PDF block.
    
    Uses file_upload type with the uploaded file ID.
    If upload failed, creates a visible placeholder.
    """
    # Get file_upload ID from block attributes
    file_upload_id = notion_block.attrs.get("file_upload_id")
    upload_failed = notion_block.attrs.get("upload_failed", False)
    
    if not file_upload_id:
        if upload_failed:
            # Create a visible placeholder paragraph
            filename = _get_resource_filename(notion_block)
            return _create_failed_upload_placeholder(filename, "PDF")
        logger.warning("PDF block has no file_upload_id - skipping")
        return None
    
    return {
        "type": "pdf",
        "pdf": {
            "type": "file_upload",
            "file_upload": {
                "id": file_upload_id
            }
        }
    }


def _convert_file(notion_block) -> dict[str, Any]:
    """Convert generic file block.
    
    Uses file_upload type with the uploaded file ID.
    If upload failed, creates a visible placeholder.
    """
    # Get file_upload ID from block attributes
    file_upload_id = notion_block.attrs.get("file_upload_id")
    upload_failed = notion_block.attrs.get("upload_failed", False)
    
    if not file_upload_id:
        if upload_failed:
            # Create a visible placeholder paragraph
            filename = _get_resource_filename(notion_block)
            return _create_failed_upload_placeholder(filename, "file")
        logger.warning("File block has no file_upload_id - skipping")
        return None
    
    return {
        "type": "file",
        "file": {
            "type": "file_upload",
            "file_upload": {
                "id": file_upload_id
            }
        }
    }


def _convert_table(notion_block) -> dict[str, Any] | list[dict[str, Any]]:
    """Convert table block.
    
    Notion API requires:
    - table block with table_width and has_column_header/has_row_header
    - table_row blocks as children
    - Maximum 100 rows per table
    
    Returns:
        Single table dict or list of table dicts if split required
    """
    # Get number of columns from the table block
    num_columns = len(notion_block._columns) if hasattr(notion_block, "_columns") else 0
    
    if num_columns == 0:
        logger.warning("Table block has no columns - skipping")
        return None
    
    # Build all table rows
    all_table_rows = []
    header_row = None
    
    for idx, row in enumerate(notion_block.children):
        row_cells = []
        needs_continuation = False
        continuation_rows_data = []
        
        # Extract cell content from each column
        for col_idx, col_id in enumerate(notion_block._columns):
            cell_prop_key = f"properties.{col_id}"
            cell_properties = row.properties.get(cell_prop_key, [])
            # Convert cell properties to rich_text, returns (main_cell, continuation_chunks)
            cell_text, continuation_chunks = _convert_cell_properties(cell_properties)
            row_cells.append(cell_text)
            
            # Track if any cell needs continuation
            if continuation_chunks:
                needs_continuation = True
                # Ensure we have enough continuation rows
                while len(continuation_rows_data) < len(continuation_chunks):
                    continuation_rows_data.append([[] for _ in range(len(notion_block._columns))])
                # Store continuation data for this column
                for chunk_idx, chunk in enumerate(continuation_chunks):
                    continuation_rows_data[chunk_idx][col_idx] = chunk
        
        row_dict = {
            "type": "table_row",
            "table_row": {
                "cells": row_cells
            }
        }
        
        # First row is typically the header
        if idx == 0:
            header_row = row_dict
        else:
            all_table_rows.append(row_dict)
            
            # Add continuation rows if needed
            if needs_continuation:
                for cont_idx, cont_cells in enumerate(continuation_rows_data):
                    # Fill empty cells with empty rich_text
                    cont_cells = [
                        cell if cell else [{"type": "text", "text": {"content": ""}}]
                        for cell in cont_cells
                    ]
                    # Add warning marker to first cell of continuation row
                    if cont_cells and cont_idx == 0:  # Only mark first continuation row
                        warning_marker = {
                            "type": "text",
                            "text": {"content": "⚠️ "},
                            "annotations": {
                                "bold": False,
                                "italic": False,
                                "strikethrough": False,
                                "underline": False,
                                "code": False,
                                "color": "orange"
                            }
                        }
                        cont_cells[0] = [warning_marker] + cont_cells[0]
                    
                    all_table_rows.append({
                        "type": "table_row",
                        "table_row": {
                            "cells": cont_cells
                        }
                    })
    
    # Check if we need to split the table (Notion limit: 100 rows including header)
    # If we have a header, max data rows = 99; without header, max = 100
    max_rows_per_table = 99 if header_row else 100
    
    if len(all_table_rows) <= max_rows_per_table:
        # Single table - no split needed
        children = [header_row] + all_table_rows if header_row else all_table_rows
        return {
            "type": "table",
            "table": {
                "table_width": num_columns,
                "has_column_header": header_row is not None,
                "has_row_header": False,
                "children": children
            }
        }
    
    # Need to split into multiple tables
    num_split_tables = (len(all_table_rows) + max_rows_per_table - 1) // max_rows_per_table
    add_warning(
        f"Table with {len(all_table_rows)} rows split into {num_split_tables} separate tables "
        f"(API limit: {max_rows_per_table} rows per table)"
    )
    logger.warning(
        f"Table has {len(all_table_rows)} rows (limit: {max_rows_per_table}). "
        f"Splitting into multiple tables."
    )
    
    tables = []
    for table_idx, i in enumerate(range(0, len(all_table_rows), max_rows_per_table)):
        chunk = all_table_rows[i:i + max_rows_per_table]
        # Include header in each split table
        children = [header_row] + chunk if header_row else chunk
        
        tables.append({
            "type": "table",
            "table": {
                "table_width": num_columns,
                "has_column_header": header_row is not None,
                "has_row_header": False,
                "children": children
            }
        })
        
        # Add warning marker between split tables (except after the last one)
        if table_idx < num_split_tables - 1:
            tables.append(_create_inline_warning_marker(
                f"Table continuation {table_idx + 2} of {num_split_tables} (API limit: {max_rows_per_table} rows per table)"
            ))
    
    return tables


def _convert_cell_properties(cell_properties: list) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Convert cell properties to rich_text array.
    
    Cell properties are in notion-py format: [[text]] or [[text, [[formatting]]]]
    Notion API limit: max 100 rich_text items per cell
    
    Returns:
        Tuple of (main_cell_rich_text, continuation_chunks)
        - main_cell_rich_text: First 100 items for the main cell
        - continuation_chunks: List of 100-item chunks for continuation rows
    """
    if not cell_properties:
        return [{"type": "text", "text": {"content": ""}}], []
    
    rich_text_items = []
    for prop in cell_properties:
        if not prop or not prop[0]:
            continue
        
        text_content = prop[0]
        formatting = prop[1:] if len(prop) > 1 else []
        
        # Split long text into chunks
        if len(text_content) > 2000:
            for i in range(0, len(text_content), 2000):
                chunk = text_content[i:i+2000]
                rich_text_items.append(_create_rich_text_object(chunk, formatting))
        else:
            rich_text_items.append(_create_rich_text_object(text_content, formatting))
    
    if not rich_text_items:
        return [{"type": "text", "text": {"content": ""}}], []
    
    # Notion API limit: max 100 rich_text items per table cell
    if len(rich_text_items) > 100:
        num_continuation_rows = (len(rich_text_items) - 100 + 99) // 100
        add_warning(
            f"Table cell content split across {num_continuation_rows + 1} rows "
            f"(API limit: 100 formatting segments per cell, had {len(rich_text_items)})"
        )
        logger.warning(
            f"Table cell has {len(rich_text_items)} rich_text items, splitting into continuation rows. "
            "Content will be preserved across multiple rows."
        )
        # Split into chunks of 100
        main_cell = rich_text_items[:100]
        continuation_chunks = [
            rich_text_items[i:i+100] 
            for i in range(100, len(rich_text_items), 100)
        ]
        return main_cell, continuation_chunks
    
    return rich_text_items, []
