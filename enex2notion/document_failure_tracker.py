"""Document Failure Tracker: database for tracking failed document imports."""
import logging
from typing import Any

from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)

DOCUMENT_FAILURE_DB_TITLE = "Document import failure"


class DocumentFailureTracker:
    def __init__(self, wrapper: NotionAPIWrapper, root_id: str, exceptions_page_id: str = None, recreate: bool = False):
        """Initialize document failure tracker.
        
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
        """Get or create document failure database under Exceptions page.
        
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
                matches = self.wrapper.search_pages(DOCUMENT_FAILURE_DB_TITLE, include_databases=True)
                deleted_count = 0
                for m in matches:
                    if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                        try:
                            self.wrapper.notion.blocks.delete(block_id=m["id"])
                            deleted_count += 1
                            logger.info(f"Deleted existing document failure database: {m['id']}")
                        except Exception as e:
                            logger.warning(f"Failed to delete database {m['id']}: {e}")
                if deleted_count > 1:
                    logger.info(f"Deleted {deleted_count} duplicate document failure databases")
            except Exception as e:
                logger.warning(f"Failed to delete existing databases: {e}")
            
            # Reset recreate flag after deletion to prevent repeated deletions
            self.recreate = False
        
        # Try to find existing database - use FIRST match
        try:
            matches = self.wrapper.search_pages(DOCUMENT_FAILURE_DB_TITLE, include_databases=True)
            for m in matches:
                if m.get("object") == "database" and m.get("parent", {}).get("page_id") == exceptions_page_id:
                    self._db_id = m["id"]
                    logger.info(f"Found existing document failure database: {self._db_id}")
                    return self._db_id
        except Exception as e:
            logger.warning(f"Failed to search for existing database: {e}")
        
        # Create new database (only if none found)
        logger.info(f"Creating new document failure database under Exceptions page")
        schema = {
            "Title": {"title": {}},  # File name with extension
            "FileLocation": {"url": {}},  # URL to block where file should be imported
            "ImportSource": {"rich_text": {}},  # Parent page or database name
            "Source-Page": {"rich_text": {}},  # Source page title
            "FileDownloadLocation": {"rich_text": {}},  # Download directory on disk
            "Resolved": {"checkbox": {}},  # Default false
        }
        db = self.wrapper.create_database(exceptions_page_id, DOCUMENT_FAILURE_DB_TITLE, schema)
        self._db_id = db["id"]
        logger.info(f"Created new document failure database: {self._db_id}")
        return self._db_id

    def log_document_failure(self,
                            filename: str,
                            file_location_url: str,
                            import_source: str,
                            source_page_title: str,
                            download_location: str):
        """Log a failed document import to the database.
        
        Args:
            filename: The name of the file with extension (e.g., "document.exe")
            file_location_url: URL to the block where file should be imported
            import_source: Parent page or database name
            source_page_title: Source page title where file was referenced
            download_location: Download directory on disk where file was saved
        """
        db_id = self.ensure_db()
        
        props = {
            "Title": {"title": [{"type": "text", "text": {"content": filename}}]},
            "FileLocation": {"url": file_location_url},
            "ImportSource": {"rich_text": [{"type": "text", "text": {"content": import_source}}]},
            "Source-Page": {"rich_text": [{"type": "text", "text": {"content": source_page_title}}]},
            "FileDownloadLocation": {"rich_text": [{"type": "text", "text": {"content": download_location}}]},
            "Resolved": {"checkbox": False},
        }
        try:
            self.wrapper.create_page(parent_id=db_id, title="", properties=props)
            logger.debug(f"Logged document failure: {filename}")
        except Exception as e:
            logger.warning(f"Failed to log document failure for {filename}: {e}")
