import logging

from enex2notion.notion_api_wrapper import create_notebook_database_schema
from enex2notion.utils_exceptions import NoteUploadFailException

logger = logging.getLogger(__name__)


def get_notebook_page(wrapper, root_page_id, title):
    """Get or create notebook page.

    Args:
        wrapper: NotionAPIWrapper instance
        root_page_id: Parent page ID
        title: Notebook title

    Returns:
        Page ID string
    """
    try:
        return _get_notebook_page(wrapper, root_page_id, title)
    except Exception as e:
        raise NoteUploadFailException from e


def _get_notebook_page(wrapper, root_page_id, title):
    """Internal: Get or create notebook page."""
    # Search for existing page
    pages = wrapper.search_pages(title)

    for page in pages:
        if page.get("parent", {}).get("page_id") == root_page_id:
            logger.info(f"Found existing notebook page: {title}")
            return page["id"]

    # Create new page
    logger.info(f"Creating new notebook page: {title}")
    page = wrapper.create_page(parent_id=root_page_id, title=title)
    return page["id"]


def get_notebook_database(wrapper, root_page_id, title):
    """Get or create notebook database.

    Args:
        wrapper: NotionAPIWrapper instance
        root_page_id: Parent page ID
        title: Database title

    Returns:
        Tuple of (database_id, database_schema)
    """
    try:
        return _get_notebook_database(wrapper, root_page_id, title)
    except Exception as e:
        raise NoteUploadFailException from e


def _get_notebook_database(wrapper, root_page_id, title):
    """Internal: Get or create notebook database."""
    # Search for existing database
    # Note: Official API doesn't have direct database search by title
    # We'll search pages and filter for databases
    logger.debug(f"Searching for existing database '{title}'...")
    pages = wrapper.search_pages(title, include_databases=True)
    logger.debug(f"  Search returned {len(pages)} results")

    for page in pages:
        logger.debug(f"  Checking: object={page.get('object')}, title={page.get('title', [{}])[0].get('plain_text') if page.get('title') else 'N/A'}")
        if page.get("object") == "database" and page.get("parent", {}).get("page_id") == root_page_id:
            database_id = page["id"]
            logger.info(f"Found existing notebook database: {title}")
            logger.info(f"  Database ID: {database_id}")
            logger.info("  Fetching database schema to adapt properties...")
            
            # Fetch the actual database schema
            database = wrapper.get_database(database_id)
            schema = database.get("properties", {})
            
            logger.info(f"  ✓ Retrieved schema with {len(schema)} properties")
            logger.info(f"  Properties: {list(schema.keys())}")
            return database_id, schema

    # Create new database
    logger.info(f"Creating new notebook database: {title}")
    schema = create_notebook_database_schema()
    
    try:
        database = wrapper.create_database(parent_id=root_page_id, title=title, properties_schema=schema)
        database_id = database["id"]
        logger.info(f"  ✓ Created database with {len(schema)} properties")
        logger.info(f"  Database ID: {database_id}")
        logger.info("")
        logger.info("  NOTE: New databases should inherit Integration access from parent.")
        logger.info("        If upload fails with 404, manually share the database:")
        logger.info("        1. Open the database in Notion")
        logger.info("        2. Click '...' → 'Add connections'  ")
        logger.info("        3. Select your Integration")
        logger.info("")
        
        # Longer delay to ensure database is fully propagated
        import time
        logger.info("  Waiting 3 seconds for Notion to propagate permissions...")
        time.sleep(3)
        
        return database_id, schema
    except Exception as e:
        error_msg = str(e).lower()
        if "object not found" in error_msg or "could not find" in error_msg:
            logger.error("")
            logger.error("✗ PERMISSION ERROR: Integration cannot access the root page")
            logger.error("")
            logger.error("SOLUTION: Share the root page with your Integration")
            logger.error("  1. Open the root page in Notion")
            logger.error("  2. Click '...' menu → 'Add connections'")
            logger.error("  3. Select your Integration")
            logger.error("  4. Make sure the connection is active")
            logger.error("")
        elif "validation" in error_msg:
            logger.error("")
            logger.error("✗ API REQUEST ERROR: Invalid database creation request")
            logger.error(f"  Details: {e}")
            logger.error("")
        raise
