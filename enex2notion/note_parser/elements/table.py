from bs4 import Tag

from enex2notion.note_parser.string_extractor import extract_string
from enex2notion.notion_blocks.table import NotionTableBlock
from enex2notion.notion_blocks.text import TextProp


def parse_table(element, is_email=False):
    # Skip all tables in email content (they're just layout tables)
    if is_email:
        return None
    
    # Skip nested tables (common in HTML emails for layout)
    # Only convert top-level tables that likely contain actual data
    if _is_nested_table(element):
        return None
    
    rows = _convert_table_into_rows(element)

    if not rows:
        return None

    table = NotionTableBlock(columns=len(rows[0]))

    for row in rows:
        table.add_row(row)

    return table


def _is_nested_table(table: Tag) -> bool:
    """Check if this table is nested inside another table.
    
    Nested tables are commonly used for HTML email layouts, not data.
    """
    parent = table.parent
    while parent:
        if parent.name == "table":
            return True
        parent = parent.parent
    return False


def _convert_table_into_rows(table: Tag):
    # Only get direct descendant rows, not nested table rows
    rows = []
    for t_row in table.find_all("tr", recursive=False):
        # Also check tbody/thead/tfoot
        if t_row.parent.name in ("tbody", "thead", "tfoot"):
            if t_row.parent.parent == table:
                row_cells = [extract_string(t_column) for t_column in t_row.find_all("td")]
                rows.append(row_cells)
        elif t_row.parent == table:
            row_cells = [extract_string(t_column) for t_column in t_row.find_all("td")]
            rows.append(row_cells)
    
    # Fall back to original behavior if no direct rows found
    if not rows:
        rows = [
            [extract_string(t_column) for t_column in t_row.find_all("td")]
            for t_row in table.find_all("tr")
        ]

    if not rows:
        return []

    # pad rows, since notion can't do colspan
    longest_row = max(len(r) for r in rows)
    for row in rows:
        empty_cells = range(longest_row - len(row))
        row.extend([TextProp("") for _ in empty_cells])

    return rows
