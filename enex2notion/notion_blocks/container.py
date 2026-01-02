from enex2notion import notion_block_types as block

from enex2notion.notion_blocks.text import NotionTextBased


class NotionCodeBlock(NotionTextBased):
    type = block.CodeBlock

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.attrs["language"] = "plain text"  # Notion API requires lowercase
        self.attrs["wrap"] = True


class NotionCalloutBlock(NotionTextBased):
    type = block.CalloutBlock

    def __init__(self, icon, **kwargs):
        super().__init__(**kwargs)

        self.attrs["icon"] = icon
