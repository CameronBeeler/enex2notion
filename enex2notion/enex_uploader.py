import logging

from tqdm import tqdm

from enex2notion.enex_types import EvernoteNote
from enex2notion.image_handler import upload_image_to_notion
from enex2notion.notion_block_converter import convert_block_to_api_format
from enex2notion.notion_api_wrapper import note_to_database_properties
from enex2notion.notion_blocks.uploadable import NotionImageBlock
from enex2notion.utils_exceptions import NoteUploadFailException

logger = logging.getLogger(__name__)

PROGRESS_BAR_WIDTH = 80


def upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, keep_failed, is_database=False, database_schema=None):
    """Upload note to Notion using official API.

    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Parent page/database ID
        note: EvernoteNote object
        note_blocks: List of block objects
        keep_failed: Whether to keep failed uploads
        is_database: True if uploading to database, False for page
        database_schema: Database schema dict for adapting properties
    """
    try:
        _upload_note(wrapper, root_id, note, note_blocks, keep_failed, is_database, database_schema)
    except Exception as e:
        error_msg = str(e).lower()
        if "is not a property that exists" in error_msg:
            logger.error("")
            logger.error("✗ DATABASE SCHEMA MISMATCH")
            logger.error("  The database exists but has the wrong properties.")
            logger.error("")
            logger.error("SOLUTION: Delete the existing database and retry")
            logger.error("  1. Open Notion")
            logger.error("  2. Find and delete the database under 'Evernote ENEX Import'")
            logger.error("  3. Run this command again")
            logger.error("")
        elif "could not find" in error_msg or "make sure the relevant" in error_msg:
            logger.error("")
            logger.error("✗ DATABASE ACCESS ERROR")
            logger.error("  The database was created but the Integration doesn't have access.")
            logger.error("")
            logger.error("SOLUTION: The database should inherit permissions from the root page.")
            logger.error("  If this persists:")
            logger.error("  1. Open the database in Notion")
            logger.error("  2. Click '...' → 'Add connections'")
            logger.error("  3. Select your Integration")
            logger.error("  4. Or delete the database and let it be recreated")
            logger.error("")
        raise NoteUploadFailException from e


def _process_image_blocks(blocks, notion_api):
    """Process image blocks recursively: upload to Notion and set file_upload IDs.
    
    Args:
        blocks: List of blocks to process
        notion_api: NotionAPIWrapper instance for uploading
    """
    for block in blocks:
        if isinstance(block, NotionImageBlock):
            # Upload image to Notion
            upload_id = upload_image_to_notion(block.resource, notion_api)
            if upload_id:
                # Set file_upload ID in attrs so converter can use it
                block.attrs["file_upload_id"] = upload_id
                logger.debug(f"Set image file_upload ID: {upload_id}")
            else:
                logger.warning("Failed to upload image block")
        
        # Process children recursively
        if hasattr(block, "children") and block.children:
            _process_image_blocks(block.children, notion_api)


def _upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, keep_failed, is_database, database_schema):
    """Internal: Upload note."""
    logger.debug(f"Creating new page for note '{note.title}'")
    logger.debug(f"  Parent ID: {root_id}")
    logger.debug(f"  Is database: {is_database}")
    logger.debug(f"  Database schema received: {database_schema}")

    # Create page
    if is_database:
        properties = note_to_database_properties(note, database_schema)
        logger.debug(f"  Generated properties: {properties}")
        new_page = wrapper.create_page(parent_id=root_id, title=note.title, properties=properties)
    else:
        new_page = wrapper.create_page(parent_id=root_id, title=note.title)

    page_id = new_page["id"]

    # Process image blocks: upload to Notion and set file_upload IDs before conversion
    _process_image_blocks(note_blocks, wrapper)

    # Convert blocks to API format
    api_blocks = []
    for block in note_blocks:
        converted = convert_block_to_api_format(block)
        if converted:
            api_blocks.append(converted)

    # Upload blocks in batches
    progress_iter = tqdm(
        iterable=range(0, len(api_blocks), 100),
        total=(len(api_blocks) + 99) // 100,
        unit="batch",
        leave=False,
        ncols=PROGRESS_BAR_WIDTH,
    )

    try:
        for start_idx in progress_iter:
            batch = api_blocks[start_idx : start_idx + 100]
            wrapper.append_blocks(block_id=page_id, children=batch)

    except Exception as e:
        if not keep_failed:
            # Archive the page (official API doesn't have remove)
            try:
                wrapper.client.pages.update(page_id=page_id, archived=True)
            except:
                pass
        raise

    logger.debug(f"Successfully uploaded note '{note.title}' with {len(api_blocks)} blocks")
