import logging
import sys

from notion_client import Client
from notion_client.errors import APIResponseError

from enex2notion.notion_api_wrapper import NotionAPIWrapper
from enex2notion.utils_exceptions import BadTokenException

logger = logging.getLogger(__name__)


def get_root(token, name):
    """Get or create root page for imports.

    Args:
        token: Notion Integration token
        name: Root page name

    Returns:
        Tuple of (NotionAPIWrapper instance, root page ID), or (None, None) for dry run
    """
    if not token:
        logger.warning(
            "No token provided, dry run mode. Nothing will be uploaded to Notion!"
        )
        return None, None

    try:
        wrapper = get_notion_wrapper(token)
    except BadTokenException:
        logger.error("Invalid Integration token provided!")
        logger.error("Create an Integration at: https://www.notion.com/my-integrations")
        sys.exit(1)

    root_id = get_import_root(wrapper, name)
    return wrapper, root_id


def get_notion_wrapper(token):
    """Initialize Notion API wrapper with Integration token.

    Args:
        token: Notion Integration token

    Returns:
        NotionAPIWrapper instance

    Raises:
        BadTokenException: If token is invalid
    """
    logger.info("Validating Integration token...")
    
    try:
        wrapper = NotionAPIWrapper(auth_token=token)
        # Test authentication and get bot info
        bot_info = wrapper.client.users.me()
        
        # Log successful validation
        bot_type = bot_info.get("type", "unknown")
        bot_name = bot_info.get("name", "Unknown")
        
        if bot_type == "bot":
            logger.info(f"✓ Token validated successfully")
            logger.info(f"  Integration: {bot_name}")
            logger.info(f"  Bot ID: {bot_info.get('id', 'N/A')}")
        else:
            logger.warning(f"Token type is '{bot_type}', expected 'bot'")
        
        return wrapper
    except APIResponseError as e:
        if e.status == 401:
            logger.error("✗ Token validation failed: Invalid or expired token")
            logger.error("  Make sure your Integration token is correct and hasn't been revoked")
            raise BadTokenException
        logger.error(f"✗ Token validation failed: {e}")
        raise


def get_import_root(wrapper, title):
    """Get or create root page for imports.

    Args:
        wrapper: NotionAPIWrapper instance
        title: Page title to find or create

    Returns:
        Root page ID as string
    """
    logger.info(f"Searching for root page '{title}'...")
    
    # Search for existing page
    pages = wrapper.search_pages(title)

    if pages:
        page_id = pages[0]["id"]
        logger.info(f"✓ Root page '{title}' found: {page_id}")
        logger.info("  Verifying Integration has access to this page...")
        
        # Actually try to retrieve the page to verify access
        try:
            wrapper.client.pages.retrieve(page_id=page_id)
            logger.info("  ✓ Integration has access to root page")
            logger.info("")
            logger.info("  IMPORTANT: New databases will be created under this page.")
            logger.info("             They should inherit Integration access automatically.")
            logger.info("             If you get 404 errors, manually share the database.")
            logger.info("")
        except APIResponseError as e:
            if e.status == 404:
                logger.error("")
                logger.error("✗ CRITICAL: Integration CANNOT access root page!")
                logger.error("")
                logger.error(f"  The page '{title}' exists but is not shared with your Integration.")
                logger.error("")
                logger.error("SOLUTION:")
                logger.error(f"  1. Open '{title}' in Notion")
                logger.error("  2. Click '...' menu → 'Add connections'")
                logger.error("  3. Select your Integration")
                logger.error("  4. Run this command again")
                logger.error("")
                sys.exit(1)
            raise
        
        return page_id

    # Page not found - need to create in shared space
    # User must have already shared a page with the Integration
    logger.error(f"✗ Root page '{title}' not found and cannot be auto-created.")
    logger.error("")
    logger.error("SOLUTION: The Integration needs a page to write to.")
    logger.error(f"  1. Create a page in Notion named '{title}'")
    logger.error("  2. Share it with your Integration:")
    logger.error("     - Open the page in Notion")
    logger.error("     - Click '...' menu → 'Add connections'")
    logger.error("     - Select your Integration")
    logger.error("  3. Run this command again")
    logger.error("")
    sys.exit(1)
