"""Handle partial imports - create error summaries and source bookmarks."""

from enex2notion.notion_blocks.container import NotionCalloutBlock
from enex2notion.notion_blocks.minor import NotionBookmarkBlock
from enex2notion.notion_blocks.text import TextProp


def create_error_summary_block(errors: list[str]) -> NotionCalloutBlock:
    """Create a warning callout block summarizing import errors.
    
    Args:
        errors: List of error messages
        
    Returns:
        NotionCalloutBlock with red warning icon and error list
    """
    if not errors:
        return None
    
    # Format errors as bulleted list
    error_text = "⚠️ Import Warnings\n\n"
    error_text += "This note was partially imported. The following issues occurred:\n\n"
    for error in errors:
        error_text += f"• {error}\n"
    
    return NotionCalloutBlock(
        icon="⚠️",
        text_prop=TextProp(error_text.strip())
    )


def create_source_bookmark(url: str) -> NotionBookmarkBlock | None:
    """Create a bookmark block for the original source URL.
    
    Args:
        url: Source URL from Evernote note
        
    Returns:
        NotionBookmarkBlock if URL is valid, None otherwise
    """
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()
    if not url:
        return None
    
    # Basic validation - must be http/https
    if not url.startswith(("http://", "https://")):
        return None
    
    return NotionBookmarkBlock(link=url)
