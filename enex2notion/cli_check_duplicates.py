"""CLI implementation for check-duplicates command."""
import json
import logging
from pathlib import Path

from enex2notion.exception_tracker import ExceptionTracker
from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)


def check_duplicates_command(wrapper: NotionAPIWrapper, root_id: str, args):
    """Execute the duplicate checking command.
    
    Scans workspace for duplicate and blank page titles, creates DuplicatePageNames
    report, and saves cleaned canonical.json.
    
    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Root page ID to scan
        args: Parsed command-line arguments
    """
    logger.info("=" * 80)
    logger.info("CHECK DUPLICATES COMMAND")
    logger.info("=" * 80)
    logger.info(f"Root page ID: {root_id}")
    
    # Determine queue directory
    queue_dir = getattr(args, "queue_directory", None)
    deprecated_queue = getattr(args, "queue", None)
    if not queue_dir and deprecated_queue:
        queue_dir = deprecated_queue.parent
    if not queue_dir:
        queue_dir = Path.home() / "Downloads" / "imports"
    queue_dir.mkdir(parents=True, exist_ok=True)
    
    canonical_path = queue_dir / "canonical.json"
    
    print("\n" + "=" * 80)
    print("STEP 1: Collecting all workspace pages")
    print("=" * 80)
    
    # Collect all accessible pages from workspace
    print("\nScanning workspace (batches of 500)...")
    page_map = {}
    
    def batch_handler(batch):
        page_map.update(batch)
        print(f"  Collected {len(page_map)} pages so far...")
    
    page_map = wrapper.list_all_accessible_pages_batched(
        batch_size=500, 
        batch_callback=batch_handler
    )
    
    print(f"\n✓ Found {len(page_map)} total pages")
    logger.info(f"Found {len(page_map)} total pages")
    
    # Step 2: Identify duplicates and blank titles
    print("\n" + "=" * 80)
    print("STEP 2: Identifying duplicates and blank titles")
    print("=" * 80)
    
    title_to_ids = {}
    for pid, title in page_map.items():
        title_to_ids.setdefault(title, []).append(pid)
    
    # Find blank titles (None or empty string)
    blank_ids = title_to_ids.get(None, []) + title_to_ids.get("", [])
    
    # Find duplicate titles (more than 1 page with same title)
    duplicates = {t: ids for t, ids in title_to_ids.items() if t and len(ids) > 1}
    
    # Collect all page IDs to remove from canonical
    ids_to_remove = set()
    
    # Add blank titles to duplicates
    if blank_ids:
        duplicates[None] = blank_ids  # Use None as key for blanks
        ids_to_remove.update(blank_ids)
        print(f"\n  Found {len(blank_ids)} pages with blank/empty titles")
    
    # Add all duplicate page IDs to removal set
    for title, ids in duplicates.items():
        if title:  # Skip None (already counted)
            ids_to_remove.update(ids)
    
    if duplicates:
        duplicate_count = len([k for k in duplicates.keys() if k])
        print(f"  Found {duplicate_count} duplicate title groups")
        print(f"  Total pages with duplicates or blank titles: {len(ids_to_remove)}")
    else:
        print("\n✓ No duplicates or blank titles found!")
    
    # Step 3: Create DuplicatePageNames report
    if duplicates:
        print("\n" + "=" * 80)
        print("STEP 3: Creating DuplicatePageNames report")
        print("=" * 80)
        
        tracker = ExceptionTracker(wrapper, root_id)
        tracker.track_duplicate_page_names(duplicates)
        
        print(f"\n✓ Logged {len(duplicates)} groups to: Exceptions → DuplicatePageNames")
        logger.info(f"Created DuplicatePageNames report with {len(duplicates)} groups")
    
    # Step 4: Save cleaned canonical
    print("\n" + "=" * 80)
    print("STEP 4: Saving cleaned canonical")
    print("=" * 80)
    
    # Remove duplicates and blanks from page_map
    for pid in ids_to_remove:
        page_map.pop(pid, None)
    
    print(f"\n  Removed {len(ids_to_remove)} pages from canonical")
    print(f"  Canonical now contains {len(page_map)} unique pages")
    
    # Sort by title (case-insensitive) and save
    try:
        sorted_items = sorted(page_map.items(), key=lambda x: (x[1] or "").lower())
        sorted_page_map = dict(sorted_items)
        
        with open(canonical_path, "w", encoding="utf-8") as f:
            json.dump(sorted_page_map, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Saved cleaned canonical to: {canonical_path}")
        logger.info(f"Saved cleaned canonical to: {canonical_path}")
    except Exception as e:
        print(f"\n✗ ERROR: Failed to save canonical: {e}")
        logger.error(f"Failed to save canonical: {e}")
        return
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Total pages scanned:        {len(page_map) + len(ids_to_remove)}")
    print(f"  Duplicate/blank groups:     {len(duplicates)}")
    print(f"  Pages removed:              {len(ids_to_remove)}")
    print(f"  Clean canonical pages:      {len(page_map)}")
    print(f"  Report location:            Notion → Exceptions → DuplicatePageNames")
    print(f"  Canonical location:         {canonical_path}")
    print("=" * 80)
