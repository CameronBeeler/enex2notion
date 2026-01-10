import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from enex2notion.enex_types import EvernoteNote
from enex2notion.image_handler import upload_image_to_notion
from enex2notion.notion_block_converter import convert_block_to_api_format
from enex2notion.notion_api_wrapper import note_to_database_properties
from enex2notion.notion_blocks.uploadable import NotionImageBlock, NotionPDFBlock, NotionFileBlock
from enex2notion.parse_warnings import init_warnings, get_warnings, clear_warnings
from enex2notion.partial_import_handler import create_error_summary_block, create_source_bookmark
from enex2notion.utils_exceptions import NoteUploadFailException

logger = logging.getLogger(__name__)

PROGRESS_BAR_WIDTH = 80


def upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, errors, is_database=False, database_schema=None, rejected_tracker=None, notebook_name="", unsupported_dir=None):
    """Upload note to Notion using official API.

    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Parent page/database ID
        note: EvernoteNote object
        note_blocks: List of block objects
        errors: List of error messages from parsing
        is_database: True if uploading to database, False for page
        database_schema: Database schema dict for adapting properties
        rejected_tracker: RejectedFilesTracker instance (optional)
        notebook_name: Name of notebook for tracking rejected files
        unsupported_dir: Directory to save unsupported files (optional)
    
    Returns:
        Tuple of (page_id, had_errors, errors) where:
        - page_id: Notion page ID
        - had_errors: True if partial import
        - errors: Updated list of all errors/warnings
    """
    try:
        return _upload_note(wrapper, root_id, note, note_blocks, errors, is_database, database_schema, rejected_tracker, notebook_name, unsupported_dir)
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


def _collect_uploadable_blocks(blocks, uploadable_list):
    """Recursively collect all uploadable blocks (images, PDFs, files).
    
    Args:
        blocks: List of blocks to scan
        uploadable_list: Output list to append uploadable blocks to
    """
    for block in blocks:
        if isinstance(block, (NotionImageBlock, NotionPDFBlock, NotionFileBlock)):
            uploadable_list.append(block)
        
        # Process children recursively
        if hasattr(block, "children") and block.children:
            _collect_uploadable_blocks(block.children, uploadable_list)


def _upload_single_file(block, notion_api, rejected_tracker, notebook_name, note_title, unsupported_dir):
    """Upload a single file block to Notion.
    
    Args:
        block: Block to upload (Image, PDF, or File)
        notion_api: NotionAPIWrapper instance
        rejected_tracker: RejectedFilesTracker instance (optional)
        notebook_name: Name of notebook for tracking
        note_title: Title of note for tracking
        unsupported_dir: Directory to save unsupported files (optional)
        
    Returns:
        Tuple of (block, upload_id, warnings) where upload_id is None if failed,
        and warnings is a list of warning messages from this upload
    """
    # Initialize warnings for this thread
    from enex2notion.parse_warnings import init_warnings, get_warnings, clear_warnings
    clear_warnings()
    init_warnings()
    
    upload_id = upload_image_to_notion(
        block.resource, notion_api, rejected_tracker, notebook_name, note_title, unsupported_dir
    )
    
    # Collect warnings from this thread
    warnings = get_warnings()
    
    return (block, upload_id, warnings)


def _process_image_blocks(blocks, notion_api, rejected_tracker=None, notebook_name="", note_title="", unsupported_dir=None):
    """Process uploadable blocks: upload to Notion concurrently and set file_upload IDs.
    
    Handles images, PDFs, and generic files with concurrent uploads (max 3 workers
    to respect Notion's ~3 req/s rate limit).
    
    Returns:
        List of warnings collected from all file uploads
    
    Args:
        blocks: List of blocks to process
        notion_api: NotionAPIWrapper instance for uploading
        rejected_tracker: RejectedFilesTracker instance (optional)
        notebook_name: Name of notebook for tracking rejected files
        note_title: Title of note for tracking rejected files
        unsupported_dir: Directory to save unsupported files (optional)
    """
    from enex2notion.parse_warnings import add_warning
    
    # Collect all uploadable blocks
    uploadable_blocks = []
    _collect_uploadable_blocks(blocks, uploadable_blocks)
    
    if not uploadable_blocks:
        return []
    
    # Upload files concurrently with thread pool (max 3 workers for rate limiting)
    logger.debug(f"Uploading {len(uploadable_blocks)} files concurrently...")
    
    all_warnings = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all upload tasks
        future_to_block = {
            executor.submit(
                _upload_single_file, block, notion_api, rejected_tracker, notebook_name, note_title, unsupported_dir
            ): block
            for block in uploadable_blocks
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_block):
            try:
                block, upload_id, warnings = future.result()
                
                # Collect warnings from worker thread
                if warnings:
                    all_warnings.extend(warnings)
                    # Also add to main thread's warning context
                    for warning in warnings:
                        add_warning(warning)
                
                if upload_id:
                    block.attrs["file_upload_id"] = upload_id
                    block_type = block.__class__.__name__
                    logger.debug(f"Uploaded {block_type}, file_upload ID: {upload_id}")
                else:
                    # Mark block as failed so we can add a placeholder
                    block.attrs["upload_failed"] = True
                    block_type = block.__class__.__name__
                    # Check if this is an unsupported file (has download_location) vs actual error
                    if hasattr(block, 'resource') and hasattr(block.resource, 'attrs'):
                        download_loc = block.resource.attrs.get('download_location')
                        if download_loc:
                            # Unsupported file - already logged at INFO level with path
                            logger.debug(f"{block_type} saved to disk (unsupported type)")
                        else:
                            # Actual upload failure
                            logger.warning(f"Failed to upload {block_type}")
                    else:
                        # Fallback: treat as failure
                        logger.warning(f"Failed to upload {block_type}")
            except Exception as e:
                logger.error(f"File upload failed with exception: {e}")
    
    return all_warnings


def _upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, errors, is_database, database_schema, rejected_tracker, notebook_name, unsupported_dir=None):
    """Internal: Upload note with partial import support.
    
    Note: Failed pages are always kept (marked as partial imports) - no deletion on error.
    """
    # Initialize warnings for conversion phase
    clear_warnings()
    init_warnings()
    
    # CRITICAL: Ensure title is never blank/None to prevent orphaned pages
    if not note.title or not note.title.strip():
        note.title = "[Untitled Note]"
        logger.warning(f"Note had blank title - assigned default: {note.title}")
        if not errors:
            errors = []
        if "Note had no title" not in str(errors):
            errors.append("Note had no title in Evernote - assigned default title")
    
    logger.debug(f"Creating new page for note '{note.title}'")
    logger.debug(f"  Parent ID: {root_id}")
    logger.debug(f"  Is database: {is_database}")
    logger.debug(f"  Database schema received: {database_schema}")
    logger.debug(f"  Errors: {len(errors)} errors" if errors else "  No errors")
    
    # Determine if this is a partial import
    has_errors = bool(errors)
    
    # Prepend error summary and source bookmark if there are errors
    if has_errors:
        # Create error summary block
        error_block = create_error_summary_block(errors)
        if error_block:
            note_blocks.insert(0, error_block)
        
        # Add source bookmark if webclip with URL
        if note.url:
            bookmark_block = create_source_bookmark(note.url)
            if bookmark_block:
                # Insert after error summary
                insert_pos = 1 if error_block else 0
                note_blocks.insert(insert_pos, bookmark_block)

    # Create page
    if is_database:
        properties = note_to_database_properties(note, database_schema, partial_import=has_errors)
        logger.debug(f"  Generated properties: {properties}")
        new_page = wrapper.create_page(parent_id=root_id, title=note.title, properties=properties)
    else:
        new_page = wrapper.create_page(parent_id=root_id, title=note.title)

    page_id = new_page["id"]

    # Process uploadable blocks (images, PDFs, files): upload to Notion and set file_upload IDs
    file_upload_warnings = _process_image_blocks(note_blocks, wrapper, rejected_tracker, notebook_name, note.title, unsupported_dir)
    
    # Merge file upload warnings with errors
    if file_upload_warnings:
        errors = list(errors) if errors else []
        errors.extend(file_upload_warnings)
        has_errors = True

    # Convert blocks to API format
    api_blocks = []
    for block in note_blocks:
        converted = convert_block_to_api_format(block)
        if converted:
            # Tables may return a list if they were split
            if isinstance(converted, list):
                api_blocks.extend(converted)
            else:
                api_blocks.append(converted)
    
    # Collect any warnings from conversion phase
    conversion_warnings = get_warnings()
    if conversion_warnings:
        # Merge with existing errors
        errors = list(errors) if errors else []
        errors.extend(conversion_warnings)
        # Update has_errors flag
        has_errors = bool(errors)
        
    # Update page properties and error summary if we have any errors
    if has_errors:
        # Update database properties to set partial import flag
        if is_database:
            logger.debug("  Updating page to mark as partial import")
            try:
                properties = note_to_database_properties(note, database_schema, partial_import=True)
                wrapper.client.pages.update(page_id=page_id, properties=properties)
            except Exception as e:
                logger.warning(f"Failed to update partial import flag: {e}")
        
        # Remove old error block if it exists
        if api_blocks and api_blocks[0].get("type") == "callout":
            api_blocks.pop(0)
        
        # Create updated error block with all errors
        error_block = create_error_summary_block(errors)
        if error_block:
            error_block_api = convert_block_to_api_format(error_block)
            if error_block_api:
                api_blocks.insert(0, error_block_api)

    # Upload blocks in batches
    progress_iter = tqdm(
        iterable=range(0, len(api_blocks), 100),
        total=(len(api_blocks) + 99) // 100,
        unit="batch",
        leave=False,
        ncols=PROGRESS_BAR_WIDTH,
    )

    block_upload_error = None
    max_retries = 3
    
    try:
        for start_idx in progress_iter:
            batch = api_blocks[start_idx : start_idx + 100]
            
            # Retry logic for this batch
            for attempt in range(max_retries):
                try:
                    wrapper.append_blocks(block_id=page_id, children=batch)
                    break  # Success - move to next batch
                except Exception as batch_error:
                    is_last_attempt = (attempt == max_retries - 1)
                    
                    # Check if it's a transient error worth retrying
                    error_msg = str(batch_error).lower()
                    is_transient = any(x in error_msg for x in [
                        "timeout", "connection", "rate limit", "429", "503", "502", "500"
                    ])
                    
                    if is_transient and not is_last_attempt:
                        wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                        logger.warning(
                            f"Transient error uploading block batch (attempt {attempt + 1}/{max_retries}): {batch_error}"
                        )
                        time.sleep(wait_time)
                        continue
                    
                    # Last attempt or permanent error - raise it
                    raise batch_error
    except Exception as e:
        # Block upload failed - keep page as partial import
        block_upload_error = str(e)
        logger.warning(f"Block upload failed after retries: {block_upload_error}")
        
        # Add error to list
        errors = list(errors) if errors else []
        errors.append(f"Failed to upload blocks: {block_upload_error}")
        has_errors = True
        
        # Update page to mark as partial import
        if is_database:
            try:
                properties = note_to_database_properties(note, database_schema, partial_import=True)
                wrapper.client.pages.update(page_id=page_id, properties=properties)
                logger.debug("  Marked page as partial import due to block upload failure")
            except Exception as update_err:
                logger.warning(f"Failed to update partial import flag: {update_err}")

    if block_upload_error:
        logger.warning(f"Note '{note.title}' uploaded with errors (page created but some blocks failed)")
    else:
        logger.debug(f"Successfully uploaded note '{note.title}' with {len(api_blocks)} blocks")
    
    return (page_id, has_errors, errors)
