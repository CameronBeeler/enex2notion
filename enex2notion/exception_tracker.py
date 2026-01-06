"""Exception tracking for partial imports.

Manages the "Exceptions" summary page structure with real-time updates:
- Root Page → Exceptions (page) → Notebook.enex (pages) → Links to partial import notes
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ExceptionTracker:
    """Tracks partial imports and maintains exception summary pages."""

    def __init__(self, wrapper, root_id: str):
        """Initialize exception tracker.

        Args:
            wrapper: NotionAPIWrapper instance
            root_id: Root page ID for creating exception pages
        """
        self.wrapper = wrapper
        self.root_id = root_id
        self._exceptions_page_id = None
        self._notebook_exception_pages = {}  # notebook_name -> page_id

    def ensure_exceptions_page(self) -> str:
        """Get or create the main "Exceptions" page under root.

        Returns:
            Exception page ID
        """
        if self._exceptions_page_id:
            return self._exceptions_page_id

        # Search for existing "Exceptions" page
        logger.debug("Searching for existing 'Exceptions' page...")
        pages = self.wrapper.search_pages("Exceptions")

        for page in pages:
            if page.get("parent", {}).get("page_id") == self.root_id:
                self._exceptions_page_id = page["id"]
                logger.info("Found existing 'Exceptions' summary page")
                return self._exceptions_page_id

        # Create new exceptions page
        logger.info("Creating 'Exceptions' summary page...")
        page = self.wrapper.create_page(parent_id=self.root_id, title="Exceptions")
        self._exceptions_page_id = page["id"]

        # Add intro paragraph
        intro_blocks = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "This page lists all partially imported notes with errors. "
                                "Each notebook has a child page listing its exceptions."
                            },
                        }
                    ]
                },
            }
        ]
        self.wrapper.append_blocks(block_id=self._exceptions_page_id, children=intro_blocks)

        return self._exceptions_page_id

    def ensure_notebook_exception_page(self, notebook_name: str) -> str:
        """Get or create exception page for a specific notebook.

        Args:
            notebook_name: Notebook name (e.g., "MyNotebook.enex")

        Returns:
            Notebook exception page ID
        """
        if notebook_name in self._notebook_exception_pages:
            return self._notebook_exception_pages[notebook_name]

        exceptions_page_id = self.ensure_exceptions_page()

        # Search for existing notebook exception page as child of Exceptions page
        page_title = f"{notebook_name}"
        logger.debug(f"Searching for existing exception page for '{page_title}'...")
        pages = self.wrapper.search_pages(page_title)

        for page in pages:
            if page.get("parent", {}).get("page_id") == exceptions_page_id:
                page_id = page["id"]
                self._notebook_exception_pages[notebook_name] = page_id
                logger.debug(f"Found existing notebook exception page: {page_title}")
                return page_id

        # Create new notebook exception page
        logger.debug(f"Creating notebook exception page: {page_title}")
        page = self.wrapper.create_page(parent_id=exceptions_page_id, title=page_title)
        page_id = page["id"]
        self._notebook_exception_pages[notebook_name] = page_id

        # Add intro paragraph
        intro_blocks = [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Partial Import Exceptions"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "Notes below encountered errors during import. "
                                "Click each link to see the note with inline error details."
                            },
                        }
                    ]
                },
            },
            {"object": "block", "type": "divider", "divider": {}},
        ]
        self.wrapper.append_blocks(block_id=page_id, children=intro_blocks)

        return page_id

    def track_partial_import(
        self, notebook_name: str, note_title: str, page_id: str, errors: list[str]
    ):
        """Record a partial import exception and append to notebook exception page.

        Args:
            notebook_name: Name of notebook
            note_title: Title of note that had partial import
            page_id: Notion page ID of the partially imported note
            errors: List of error messages
        """
        notebook_exception_page_id = self.ensure_notebook_exception_page(notebook_name)

        # Create blocks to append
        blocks = []

        # Bullet point with link to note
        blocks.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "mention",
                            "mention": {"type": "page", "page": {"id": page_id}},
                            "annotations": {"bold": True},
                        },
                        {"type": "text", "text": {"content": f" - {note_title}"}},
                    ]
                },
            }
        )

        # Indented error messages as nested bullets
        if errors:
            error_items = []
            for error in errors[:10]:  # Limit to first 10 errors to avoid huge lists
                error_items.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": error}}],
                            "color": "red",
                        },
                    }
                )

            if len(errors) > 10:
                error_items.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": f"... and {len(errors) - 10} more errors"},
                                }
                            ],
                            "color": "gray",
                        },
                    }
                )

            # Append error items as children to the main bullet
            # Note: Official API requires separate append for nested children
            # We'll append the main bullet first, then its children
            try:
                result = self.wrapper.append_blocks(
                    block_id=notebook_exception_page_id, children=[blocks[0]]
                )
                if result and len(result) > 0:
                    parent_block_id = result[0]["id"]
                    self.wrapper.append_blocks(block_id=parent_block_id, children=error_items)
            except Exception as e:
                logger.error(f"Failed to append exception entry: {e}")
                logger.debug(e, exc_info=e)
        else:
            # No errors to nest, just append the main bullet
            try:
                self.wrapper.append_blocks(block_id=notebook_exception_page_id, children=blocks)
            except Exception as e:
                logger.error(f"Failed to append exception entry: {e}")
                logger.debug(e, exc_info=e)

        logger.debug(f"Tracked partial import for note '{note_title}' in notebook '{notebook_name}'")

    # New: generic special exception page and unmatched link tracking
    def _ensure_special_child_page(self, title: str) -> str:
        exceptions_page_id = self.ensure_exceptions_page()
        pages = self.wrapper.search_pages(title)
        for page in pages:
            if page.get("parent", {}).get("page_id") == exceptions_page_id:
                return page["id"]
        page = self.wrapper.create_page(parent_id=exceptions_page_id, title=title)
        return page["id"]

    def track_unmatched_link(self, source_page_title: str, source_page_id: str, link_text: str, original_url: str):
        """Record an unmatched evernote link.

        Appends a bullet to Exceptions → EvernoteLinkFailure with a mention to the source page,
        the link_text used for matching, and the original URL.
        """
        page_id = self._ensure_special_child_page("EvernoteLinkFailure")
        bullet = {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
                    {"type": "text", "text": {"content": f" – '{link_text}' → {original_url}"}},
                ]
            }
        }
        try:
            self.wrapper.append_blocks(block_id=page_id, children=[bullet])
        except Exception as e:
            logger.warning(f"Failed to append unmatched link entry: {e}")

    def track_ambiguous_link(self, source_page_title: str, source_page_id: str, link_text: str, candidate_ids: list[tuple[str, str]]):
        """Record an ambiguous evernote link with multiple candidate pages.

        Appends a bullet to Exceptions → UnresolvableEvernoteLinks with a mention to the source page,
        the link_text used for matching, and a sub-list of candidate page mentions.
        """
        page_id = self._ensure_special_child_page("UnresolvableEvernoteLinks")
        # Parent bullet
        parent = {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
                    {"type": "text", "text": {"content": f" – ambiguous '{link_text}' (multiple pages found)"}},
                ]
            }
        }
        # Children bullets for candidates
        children = []
        for cid, ctitle in candidate_ids[:10]:
            children.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "mention", "mention": {"type": "page", "page": {"id": cid}}},
                        {"type": "text", "text": {"content": f" – {ctitle}"}},
                    ]
                }
            })
        try:
            result = self.wrapper.append_blocks(block_id=page_id, children=[parent])
            if result and children:
                parent_id = result[0]["id"]
                self.wrapper.append_blocks(block_id=parent_id, children=children)
        except Exception as e:
            logger.warning(f"Failed to append ambiguous link entry: {e}")

    def track_duplicate_page_names(self, duplicates: dict[str, list[str]]):
        """Record duplicate page names and link to all duplicates.

        Args:
            duplicates: mapping title -> list of page_ids with that title
        """
        page_id = self._ensure_special_child_page("DuplicatePageNames")
        blocks = []
        for title, ids in duplicates.items():
            if len(ids) < 2:
                continue
            # Parent bullet with the duplicate title
            parent = {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": title or "<untitled>"}}]
                }
            }
            blocks.append(parent)
            # Append parent, then children mentions
            try:
                res = self.wrapper.append_blocks(block_id=page_id, children=[parent])
                if res:
                    parent_block_id = res[0]["id"]
                    child_bullets = []
                    for pid in ids[:25]:
                        child_bullets.append({
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [
                                    {"type": "mention", "mention": {"type": "page", "page": {"id": pid}}}
                                ]
                            }
                        })
                    if child_bullets:
                        self.wrapper.append_blocks(block_id=parent_block_id, children=child_bullets)
            except Exception as e:
                logger.warning(f"Failed to log duplicate title '{title}': {e}")
