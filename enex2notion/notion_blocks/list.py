from enex2notion import notion_block_types as block

from enex2notion.notion_blocks.text import NotionTextBased


class NotionBulletedListBlock(NotionTextBased):
    type = block.BulletedListBlock


class NotionNumberedListBlock(NotionTextBased):
    type = block.NumberedListBlock


class NotionTodoBlock(NotionTextBased):
    type = block.TodoBlock

    def __init__(self, checked, **kwargs):
        super().__init__(**kwargs)

        self.attrs["checked"] = checked
