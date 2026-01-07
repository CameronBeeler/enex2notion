"""Review Tracker: database of processed pages for link resolution review."""
import logging
from typing import Any

from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)

REVIEW_DB_TITLE = "Link Resolution Review"


class ReviewTracker:
    def __init__(self, wrapper: NotionAPIWrapper, root_id: str, exceptions_page_id: str = None, recreate: bool = False):
        """Initialize review tracker.
        
        Args:
            wrapper: NotionAPIWrapper instance
            root_id: Root page ID
            exceptions_page_id: Parent page for database (if None, will search for Exceptions page)
            recreate: If True, delete ALL existing databases and create new one
        """
        self.wrapper = wrapper
        self.root_id = root_id
        self.exceptions_page_id = exceptions_page_id
        self.recreate = recreate
        self._db_id: str | None = None

    def _find_exceptions_page(self) -> str:
        """Find the Exceptions page under root."""
        if self.exceptions_page_id:
            return self.exceptions_page_id
        
        pages = self.wrapper.search_pages("Exceptions")
        for page in pages:
            if page.get("parent", {}).get("page_id") == self.root_id:
                self.exceptions_page_id = page["id"]
                return self.exceptions_page_id
        
        # If not found, return root_id as fallback
        logger.warning("Exceptions page not found, creating database under root")
        return self.root_id

    def ensure_db(self) -> str:
        """Get or create review database under Exceptions page."""
        if self._db_id and not self.recreate:
            return self._db_id
        
        exceptions_page_id = self._find_exceptions_page()
        
        # Delete ALL existing databases with this title if recreate requested
        if self.recreate:
            try:
                matches = self.wrapper.search_pages(REVIEW_DB_TITLE, include_databases=True)
                deleted_count = 0
                for m in matches:
                    if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                        try:
                            self.wrapper.notion.blocks.delete(block_id=m["id"])
                            deleted_count += 1
                            logger.info(f"Deleted existing review database: {m['id']}")
                        except Exception as e:
                            logger.warning(f"Failed to delete database {m['id']}: {e}")
                if deleted_count > 1:
                    logger.info(f"Deleted {deleted_count} duplicate review databases")
            except Exception as e:
                logger.warning(f"Failed to delete existing databases: {e}")
        
        # Try to find existing database (if not recreating) - use FIRST match
        if not self.recreate:
            try:
                matches = self.wrapper.search_pages(REVIEW_DB_TITLE, include_databases=True)
                for m in matches:
                    if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                        self._db_id = m["id"]
                        logger.debug(f"Found existing review database: {self._db_id}")
                        return self._db_id
            except Exception as e:
                logger.warning(f"Failed to search for existing database: {e}")
        
        # Create new database
        logger.info(f"Creating new review database under Exceptions page")
        schema = {
            "Page-Title": {"title": {}},  # The visible link text
            "Source-Page": {"rich_text": {}},  # Source page title where link was found
            "Original URL": {"url": {}},
            "Source Block URL": {"url": {}},
            "Target Page URL": {"url": {}},
            "Target Page ID": {"rich_text": {}},
            "Status": {"select": {"options": [
                {"name": "Resolved", "color": "green"},
                {"name": "Partial", "color": "yellow"},
                {"name": "Ambiguous", "color": "orange"},
                {"name": "Unresolved", "color": "red"}
            ]}},
            "Processed At": {"date": {}},
        }
        db = self.wrapper.create_database(exceptions_page_id, REVIEW_DB_TITLE, schema)
        self._db_id = db["id"]
        return self._db_id

    def log_link(self,
                 link_text: str,
                 source_page_title: str,
                 source_page_id: str,
                 original_url: str,
                 status: str,
                 source_block_id: str | None = None,
                 target_page_id: str | None = None,
                 target_page_title: str | None = None):
        """Log a page's link resolution results to the review database.
        
        Args:
            title: Source page title being processed (where links are found)
            page_id: Source page ID being processed
            matched: Number of matched links
            unmatched: Number of unmatched links
            ambiguous: Number of ambiguous links
            status: Overall status (Resolved, Partial, Ambiguous, Unresolved)
        """
        db_id = self.ensure_db()
        # Build URLs
        source_url = f"https://notion.so/{source_page_id.replace('-', '')}"
        block_url = None
        if source_block_id:
            sp = source_page_id.replace('-', '')
            block_url = f"https://www.notion.so/{sp}#{source_block_id.replace('-', '')}"
        target_url = f"https://notion.so/{target_page_id.replace('-', '')}" if target_page_id else None
        
        # Choose title text
        page_title_text = link_text or (target_page_title or original_url or "<untitled>")
        
        props = {
            "Page-Title": {"title": [{"type": "text", "text": {"content": page_title_text}}]},
            "Source-Page": {"rich_text": [{"type": "text", "text": {"content": source_page_title or ""}}]},
            "Original URL": {"url": original_url},
            "Source Block URL": {"url": block_url},
            "Target Page URL": {"url": target_url},
            "Target Page ID": {"rich_text": [{"type": "text", "text": {"content": target_page_id or ""}}]},
            "Status": {"select": {"name": status}},
            "Processed At": {"date": {"start": __import__('datetime').datetime.utcnow().isoformat() + "Z"}},
        }
        try:
            self.wrapper.create_page(parent_id=db_id, title="", properties=props)
            logger.debug(f"Logged link row: '{page_title_text}' status={status}")
        except Exception as e:
            logger.warning(f"Failed to log review link row for {source_page_id}: {e}")
