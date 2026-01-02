from enex2notion import notion_block_types as block

from enex2notion.notion_blocks.text import NotionTextBased


class NotionHeaderBlock(NotionTextBased):
    type = block.HeaderBlock


class NotionSubheaderBlock(NotionTextBased):
    type = block.SubheaderBlock


class NotionSubsubheaderBlock(NotionTextBased):
    type = block.SubsubheaderBlock
