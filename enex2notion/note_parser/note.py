import logging

from bs4 import BeautifulSoup, Tag

from enex2notion.enex_types import EvernoteNote
from enex2notion.note_parser.note_post_process_condense import condense_lines
from enex2notion.note_parser.note_post_process_resources import resolve_resources
from enex2notion.note_parser.note_type_based import parse_note_blocks_based_on_type
from enex2notion.notion_blocks.container import NotionCalloutBlock
from enex2notion.notion_blocks.text import TextProp
from enex2notion.parse_warnings import init_warnings, get_warnings, clear_warnings
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)


def parse_note(note: EvernoteNote, rules: Rules) -> tuple[list, list[str]]:
    """Parse Evernote note into Notion blocks.
    
    Args:
        note: EvernoteNote object
        rules: Parsing rules
        
    Returns:
        Tuple of (blocks, errors) where:
        - blocks: List of successfully parsed NotionBaseBlock objects
        - errors: List of error/warning messages for partial imports
    """
    # Initialize warnings collection for this parse operation
    clear_warnings()
    init_warnings()
    
    errors = []
    
    try:
        note_dom = _parse_note_dom(note)
        if note_dom is None:
            errors.append("Failed to parse note content - invalid HTML structure")
            return [], errors

        note_blocks = parse_note_blocks_based_on_type(
            note, note_dom, note.is_email
        )

        if rules.condense_lines_sparse:
            note_blocks = condense_lines(note_blocks, is_sparse=True)
        elif rules.condense_lines:
            note_blocks = condense_lines(note_blocks)

        if rules.add_meta:
            _add_meta(note_blocks, note)

        resolve_resources(note_blocks, note)
        
        # Collect any warnings from parsing phase
        warnings = get_warnings()
        if warnings:
            errors.extend(warnings)

        return note_blocks, errors
    
    except Exception as e:
        errors.append(f"Unexpected error during parsing: {str(e)}")
        # Return whatever blocks were created before the error
        return [], errors


def _parse_note_dom(note: EvernoteNote) -> Tag | None:
    # Using html.parser because Evernote enml2 is basically HTML
    note_dom = BeautifulSoup(note.content, "html.parser").find("en-note")

    if not isinstance(note_dom, Tag):
        logger.error(f"Failed to extract DOM from note '{note.title}'")
        return None

    if len(note_dom.contents) == 0:
        return None

    return _filter_yinxiang_markdown(note_dom)


def _filter_yinxiang_markdown(note_dom: Tag) -> Tag:
    last_block = note_dom.contents[-1]

    if not isinstance(last_block, Tag):
        return note_dom

    if "display:none" in last_block.attrs.get("style", ""):
        last_block.extract()

    return note_dom


def _add_meta(note_blocks, note: EvernoteNote):
    note_blocks.insert(
        0,
        NotionCalloutBlock(
            icon="ℹ️",
            text_prop=TextProp(_get_note_meta(note)),
        ),
    )


def _get_note_meta(note: EvernoteNote):
    note_meta = [
        "Created: {0}".format(note.created.isoformat()),
        "Updated: {0}".format(note.updated.isoformat()),
    ]

    if note.url:
        note_meta.append(f"URL: {note.url}")

    if note.tags:
        note_tags = ", ".join(note.tags)
        note_meta.append(f"Tags: {note_tags}")

    return "\n".join(note_meta)
