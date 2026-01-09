"""Exception tracking for partial imports.

Manages the "Exceptions" summary page structure with real-time updates:
- Root Page → Exceptions (page) → Notebook.enex (pages) → Links to partial import notes
"""
import logging
import time
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
        self._special_pages_cache = {}  # title -> page_id (cached after first lookup/create)

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
    def _ensure_special_child_page(self, title: str, recreate: bool = False) -> str:
        """Get or create a special exception child page.
        
        Args:
            title: Page title
            recreate: If True, delete ALL existing pages with this title and create new one
        
        Returns:
            Page ID
        """
        # Return cached ID if we already have it and not recreating
        if not recreate and title in self._special_pages_cache:
            return self._special_pages_cache[title]
        
        exceptions_page_id = self.ensure_exceptions_page()
        
        # Delete ALL existing pages with this title if recreate requested
        if recreate:
            pages = self.wrapper.search_pages(title)
            deleted_count = 0
            deleted_ids = []
            for page in pages:
                if page.get("parent", {}).get("page_id") == exceptions_page_id:
                    try:
                        page_id = page["id"]
                        self.wrapper.delete_block(block_id=page_id)
                        deleted_ids.append(page_id)
                        deleted_count += 1
                        logger.info(f"Deleted existing exception page: {title} ({page_id})")
                    except Exception as e:
                        logger.warning(f"Failed to delete page '{title}' ({page['id']}): {e}")
            
            if deleted_count > 0:
                if deleted_count > 1:
                    logger.info(f"Deleted {deleted_count} duplicate '{title}' pages")
                # Wait for deletion to propagate in Notion's system
                logger.debug(f"Waiting 2s for deletion to propagate...")
                time.sleep(2)
            
            # Clear cache since we deleted
            self._special_pages_cache.pop(title, None)
        
        # Find existing page (if not recreating) - use FIRST match
        if not recreate:
            pages = self.wrapper.search_pages(title)
            for page in pages:
                if page.get("parent", {}).get("page_id") == exceptions_page_id:
                    page_id = page["id"]
                    self._special_pages_cache[title] = page_id
                    logger.debug(f"Found existing exception page: {title}")
                    return page_id
        
        # Create new page
        logger.info(f"Creating new exception page: {title}")
        page = self.wrapper.create_page(parent_id=exceptions_page_id, title=title)
        page_id = page["id"]
        self._special_pages_cache[title] = page_id
        return page_id

    def track_unmatched_link(self, source_page_title: str, source_page_id: str, link_text: str, original_url: str, block_id: str = None, recreate: bool = False):
        """Record an unmatched evernote link.

        Appends a toggle to Exceptions → EvernoteLinkFailure with a mention to the source page,
        the link_text used for matching, the original URL, and optional block URL.
        """
        page_id = self._ensure_special_child_page("EvernoteLinkFailure", recreate=recreate)
        
        # Build rich_text with page mention and link details
        rich_text = [
            {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
            {"type": "text", "text": {"content": f" – '{link_text}' → {original_url}"}},
        ]
        
        # Add block URL if provided
        if block_id:
            clean_block_id = block_id.replace("-", "")
            sp = source_page_id.replace("-", "") if source_page_id else ""
            block_url = f"https://www.notion.so/{sp}#{clean_block_id}" if sp else f"https://www.notion.so/{clean_block_id}"
            rich_text.extend([
                {"type": "text", "text": {"content": " [Block: "}},
                {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                {"type": "text", "text": {"content": "]"}},
            ])
        
        toggle = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": rich_text
            }
        }
        try:
            self.wrapper.append_blocks(block_id=page_id, children=[toggle])
        except Exception as e:
            logger.warning(f"Failed to append unmatched link entry: {e}")

    def track_ambiguous_link(self, source_page_title: str, source_page_id: str, link_text: str, candidate_ids: list[tuple[str, str]], block_id: str = None, recreate: bool = False):
        """Record an ambiguous evernote link with multiple candidate pages.

        Appends a toggle to Exceptions → UnresolvableEvernoteLinks with a mention to the source page,
        the link_text used for matching, and a sub-list of candidate page mentions.
        """
        page_id = self._ensure_special_child_page("UnresolvableEvernoteLinks", recreate=recreate)
        
        # Build rich_text for toggle header
        rich_text = [
            {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
            {"type": "text", "text": {"content": f" – ambiguous '{link_text}' (multiple pages found)"}},
        ]
        
        # Add block URL if provided
        if block_id:
            clean_block_id = block_id.replace("-", "")
            sp = source_page_id.replace("-", "") if source_page_id else ""
            block_url = f"https://www.notion.so/{sp}#{clean_block_id}" if sp else f"https://www.notion.so/{clean_block_id}"
            rich_text.extend([
                {"type": "text", "text": {"content": " [Block: "}},
                {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                {"type": "text", "text": {"content": "]"}},
            ])
        
        # Parent toggle
        parent = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": rich_text
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

    def track_duplicate_page_names(self, duplicates: dict[str | None, list[str]], recreate: bool = True):
        """Record duplicate page names and link to all duplicates.

        Args:
            duplicates: mapping title -> list of page_ids with that title
                      (title can be None for blank titles)
        """
        page_id = self._ensure_special_child_page("DuplicatePageNames", recreate=recreate)
        
        for title, ids in duplicates.items():
            if len(ids) < 2 and title is not None:
                continue
            
            # Use "Blank-Page-Titles" for None/empty titles
            display_title = "Blank-Page-Titles" if title is None or title == "" else title
            
            # Parent toggle with the title
            parent = {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": display_title}}]
                }
            }
            
            # Append parent, then children with page mentions and block URLs
            try:
                res = self.wrapper.append_blocks(block_id=page_id, children=[parent])
                if res:
                    parent_block_id = res[0]["id"]
                    child_bullets = []
                    for pid in ids:
                        # Create block URL
                        clean_id = pid.replace("-", "")
                        block_url = f"https://www.notion.so/{clean_id}"
                        
                        child_bullets.append({
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [
                                    {"type": "mention", "mention": {"type": "page", "page": {"id": pid}}},
                                    {"type": "text", "text": {"content": " – "}},
                                    {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                                ]
                            }
                        })
                    if child_bullets:
                        self.wrapper.append_blocks(block_id=parent_block_id, children=child_bullets)
            except Exception as e:
                logger.warning(f"Failed to log duplicate title '{display_title}': {e}")
