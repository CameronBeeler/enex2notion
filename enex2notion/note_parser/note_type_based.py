from bs4 import Tag

from enex2notion.enex_types import EvernoteNote
from enex2notion.note_parser.blocks import parse_note_blocks
from enex2notion.notion_blocks.base import NotionBaseBlock


def parse_note_blocks_based_on_type(
    note: EvernoteNote, note_dom: Tag, is_email: bool = False
) -> list[NotionBaseBlock]:
    """Parse note blocks treating all notes as mixed-content.
    
    All notes are processed element-by-element. There is no wholesale note conversion
    based on note type, as Evernote notes can contain any combination of content types.
    """
    return parse_note_blocks(note_dom, is_email)
