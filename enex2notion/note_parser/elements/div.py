import logging
import re

from bs4 import Tag

from enex2notion.note_parser.string_extractor import extract_string
from enex2notion.notion_blocks.container import NotionCodeBlock, NotionCalloutBlock
from enex2notion.notion_blocks.list import NotionTodoBlock
from enex2notion.notion_blocks.minor import NotionBookmarkBlock
from enex2notion.notion_blocks.text import NotionTextBlock, TextProp

logger = logging.getLogger(__name__)


def parse_div(element: Tag):
    style = element.get("style", "")

    # Tasks, skipping those
    if "en-task-group" in style:
        logger.debug("Skipping task block")
        return None

    # Google drive links
    if "en-richlink" in style:
        return parse_richlink(element)

    # Code blocks
    if "en-codeblock" in style:
        return parse_codeblock(element)

    # Embedded webclips (handle as individual elements, not note-level)
    if "en-clipped-content" in style:
        return parse_embedded_webclip(element)

    # Text paragraph
    return parse_text(element)


def parse_codeblock(element: Tag):
    return NotionCodeBlock(text_prop=extract_string(element))


def parse_text(element: Tag):
    element_text = extract_string(element)

    todo = element.find("en-todo")
    if todo:
        is_checked = todo.get("checked") == "true"
        return NotionTodoBlock(text_prop=element_text, checked=is_checked)

    return NotionTextBlock(text_prop=element_text)


def parse_richlink(element: Tag):
    url = re.match(".*en-href:(.*?);", element["style"]).group(1).strip()

    return NotionBookmarkBlock(url=url)


def parse_embedded_webclip(element: Tag):
    """Parse embedded webclip div as a callout block with the source URL.
    
    Embedded webclips contain complex HTML (often entire web pages or documents).
    Instead of trying to parse all that complexity, we convert it to a callout
    that references the original source URL.
    """
    style = element.get("style", "")
    
    # Extract source URL from style attribute
    source_url_match = re.search(r"--en-clipped-source-url:\s*([^;]+)", style)
    source_title_match = re.search(r"--en-clipped-source-title:\s*([^;]+)", style)
    
    source_url = source_url_match.group(1).strip() if source_url_match else "Unknown source"
    source_title = source_title_match.group(1).strip() if source_title_match else "Embedded web clip"
    
    # Create a callout with link to the original source
    callout_text = f"📎 {source_title}\nSource: {source_url}"
    
    logger.info(f"Converting embedded webclip to callout: {source_title}")
    
    return NotionCalloutBlock(
        icon="📎",
        text_prop=TextProp(callout_text)
    )
