"""Invalid URL Tracker: database for tracking invalid/broken URLs."""
import logging
from typing import Any

from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)

INVALID_URL_DB_TITLE = "Invalid URLs"


class InvalidURLTracker:
    def __init__(self, wrapper: NotionAPIWrapper, root_id: str, exceptions_page_id: str = None, recreate: bool = False):
        """Initialize invalid URL tracker.
        
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
        self._counter: int = 0  # Counter for titles

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
        """Get or create invalid URL database under Exceptions page.
        
        CRITICAL: This caches the database ID after first call to prevent
        creating multiple databases.
        """
        # Return cached ID if already initialized
        if self._db_id:
            return self._db_id
        
        exceptions_page_id = self._find_exceptions_page()
        
        # Delete ALL existing databases with this title if recreate requested
        if self.recreate:
            try:
                matches = self.wrapper.search_pages(INVALID_URL_DB_TITLE, include_databases=True)
                deleted_count = 0
                for m in matches:
                    if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                        try:
                            self.wrapper.notion.blocks.delete(block_id=m["id"])
                            deleted_count += 1
                            logger.info(f"Deleted existing invalid URL database: {m['id']}")
                        except Exception as e:
                            logger.warning(f"Failed to delete database {m['id']}: {e}")
                if deleted_count > 1:
                    logger.info(f"Deleted {deleted_count} duplicate invalid URL databases")
            except Exception as e:
                logger.warning(f"Failed to delete existing databases: {e}")
            
            # Reset recreate flag and counter after deletion
            self.recreate = False
            self._counter = 0
        
        # Try to find existing database - use FIRST match
        try:
            matches = self.wrapper.search_pages(INVALID_URL_DB_TITLE, include_databases=True)
            for m in matches:
                if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                    self._db_id = m["id"]
                    logger.info(f"Found existing invalid URL database: {self._db_id}")
                    # TODO: Query existing database to set counter to max existing value + 1
                    # For now, just start from 1 and let it increment
                    return self._db_id
        except Exception as e:
            logger.warning(f"Failed to search for existing database: {e}")
        
        # Create new database (only if none found)
        logger.info(f"Creating new invalid URL database under Exceptions page")
        schema = {
            "Title": {"title": {}},  # Numeric counter
            "ImportSource": {"rich_text": {}},  # Parent page or database name
            "URLLocation": {"url": {}},  # URL to block where invalid URL was found
            "Source-Page": {"rich_text": {}},  # Source page title
            "Resolved": {"checkbox": {}},  # Default false
        }
        db = self.wrapper.create_database(exceptions_page_id, INVALID_URL_DB_TITLE, schema)
        self._db_id = db["id"]
        logger.info(f"Created new invalid URL database: {self._db_id}")
        return self._db_id

    def log_invalid_url(self,
                       import_source: str,
                       url_location: str,
                       source_page_title: str):
        """Log an invalid URL to the database.
        
        Args:
            import_source: Parent page or database name
            url_location: URL to the block where invalid URL was found
            source_page_title: Source page title where invalid URL was referenced
        """
        db_id = self.ensure_db()
        
        # Increment counter for each logged URL
        self._counter += 1
        
        props = {
            "Title": {"title": [{"type": "text", "text": {"content": str(self._counter)}}]},
            "ImportSource": {"rich_text": [{"type": "text", "text": {"content": import_source}}]},
            "URLLocation": {"url": url_location},
            "Source-Page": {"rich_text": [{"type": "text", "text": {"content": source_page_title}}]},
            "Resolved": {"checkbox": False},
        }
        try:
            self.wrapper.create_page(parent_id=db_id, title="", properties=props)
            logger.debug(f"Logged invalid URL #{self._counter}")
        except Exception as e:
            logger.warning(f"Failed to log invalid URL: {e}")
