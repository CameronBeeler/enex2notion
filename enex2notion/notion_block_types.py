"""Mock block type identifiers for internal block representations.

This module provides type identifiers that were previously from notion.block.
These are used only as type markers in internal block representations,
not for actual Notion API operations.
"""


class TextBlock:
    """Text/paragraph block type identifier."""
    pass


class HeaderBlock:
    """Header level 1 block type identifier."""
    pass


class SubheaderBlock:
    """Header level 2 block type identifier."""
    pass


class SubsubheaderBlock:
    """Header level 3 block type identifier."""
    pass


class BulletedListBlock:
    """Bulleted list item block type identifier."""
    pass


class NumberedListBlock:
    """Numbered list item block type identifier."""
    pass


class TodoBlock:
    """Todo/checkbox block type identifier."""
    pass


class DividerBlock:
    """Divider/horizontal rule block type identifier."""
    pass


class BookmarkBlock:
    """Bookmark/link block type identifier."""
    pass


class CodeBlock:
    """Code block type identifier."""
    pass


class QuoteBlock:
    """Quote block type identifier."""
    pass


class CalloutBlock:
    """Callout block type identifier."""
    pass


class ImageBlock:
    """Image block type identifier."""
    pass


class FileBlock:
    """File block type identifier."""
    pass


class PDFBlock:
    """PDF block type identifier."""
    pass


class VideoBlock:
    """Video block type identifier."""
    pass


class AudioBlock:
    """Audio block type identifier."""
    pass


class EmbedBlock:
    """Embed block type identifier."""
    pass


class ColumnBlock:
    """Column block type identifier."""
    pass


class ColumnListBlock:
    """Column list block type identifier."""
    pass


class TableBlock:
    """Table block type identifier."""
    pass


class TableRowBlock:
    """Table row block type identifier."""
    pass
