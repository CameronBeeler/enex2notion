"""CLI implementation for resolve-links command."""
import json
from pathlib import Path
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any

from tqdm import tqdm

from enex2notion.link_resolution_report import (
    LinkResolutionStats,
    MatchedLink,
    print_resolution_report,
    save_resolution_report,
)
from enex2notion.link_resolver import (
    find_evernote_links_in_page,
    create_updated_rich_text,
    validate_target_page,
)
from enex2notion.notion_api_wrapper import NotionAPIWrapper, _extract_page_title
from enex2notion.page_inventory_tracker import PageInventoryTracker
from enex2notion.exception_tracker import ExceptionTracker

logger = logging.getLogger(__name__)


def resolve_links_command(wrapper: NotionAPIWrapper, root_id: str, args):
    """Execute the link resolution command.
    
    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Root page ID to scan
        args: Parsed command-line arguments
    """
    stats = LinkResolutionStats()
    matched_links = []
    unmatched_links = []
    
    # Step 1: Collect all pages (workspace + root) with batched inventory tracking
    logger.info("=" * 80)
    logger.info("STEP 1: Collecting canonical page set (workspace + root)...")
    logger.info(f"Root page ID: {root_id}")
    
    # Check if we should load from/save to page list cache
    page_list_file = getattr(args, "page_list", None)
    queue_dir = getattr(args, "queue_directory", None)
    # Back-compat: infer directory from deprecated --queue if provided
    deprecated_queue = getattr(args, "queue", None)
    if not queue_dir and deprecated_queue:
        queue_dir = deprecated_queue.parent
    # Default queue directory if none provided: ~/Downloads/imports
    if not queue_dir:
        queue_dir = Path.home() / "Downloads" / "imports"
    queue_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = queue_dir / "canonical.json"
    unfinished_path = queue_dir / "unfinished.json"
    completed_path = queue_dir / "completed.json"
    
    # Check if we're doing a single-page run (--page or --page-id)
    single_page_mode = getattr(args, "page", None) or getattr(args, "page_id", None)
    
    # OPTIMIZATION: If canonical exists, always load it (skip expensive rescan)
    # User can delete canonical.json to force a fresh scan
    if canonical_path.exists() and not page_list_file:
        print(f"\nLoading existing canonical: {canonical_path}")
        logger.info(f"Single-page mode: loading canonical from {canonical_path}")
        try:
            with open(canonical_path, "r", encoding="utf-8") as f:
                page_map = json.load(f)
            logger.info(f"Loaded {len(page_map)} pages from canonical (assuming pre-cleaned)")
            print(f"  Loaded {len(page_map)} pages from canonical")
        except Exception as e:
            logger.error(f"Failed to load canonical: {e}")
            print(f"ERROR: Could not load canonical.json: {e}")
            return
    elif page_list_file and page_list_file.exists():
        print(f"\nLoading page list from cache: {page_list_file}")
        logger.info(f"Loading page list from: {page_list_file}")
        try:
            with open(page_list_file, "r", encoding="utf-8") as f:
                page_map = json.load(f)
            logger.info(f"Loaded {len(page_map)} pages from cache")
            print(f"  Loaded {len(page_map)} pages from cache")
        except Exception as e:
            logger.error(f"Failed to load page list cache: {e}")
            print(f"ERROR: Could not load cache file: {e}")
            print("  Falling back to scanning Notion...")
            # If queue-directory is provided, build canonical from workspace only (no inventory)
            if queue_dir:
                print("\nCollecting all accessible workspace pages (batches of 500)...")
                page_map = wrapper.list_all_accessible_pages_batched(batch_size=500)
            else:
                # Use batched collection with inventory view
                inventory = PageInventoryTracker(wrapper, root_id)
                inventory.create_inventory_page()
                inventory.update_status("Collecting pages...")
                
                def batch_handler(batch):
                    inventory.append_page_batch(batch, set())
                    inventory.update_status(f"Collecting pages... ({inventory.page_count} collected)")
                
                print("\nCollecting pages under root (batches of 500)...")
                pages_root = wrapper.list_all_pages_batched(root_id, batch_size=500, batch_callback=batch_handler)
                print("Collecting all accessible workspace pages (batches of 500)...")
                pages_ws = wrapper.list_all_accessible_pages_batched(batch_size=500)
                page_map = pages_root.copy(); page_map.update(pages_ws)
    else:
        # Fast approach: workspace search, then remove Exceptions page and its children
        print(f"\nCollecting pages under 'Evernote ENEX Import' (fast workspace scan)...")
        print(f"  Step 1/2: Scanning workspace (batches of 500)...")
        
        # Get all workspace pages quickly
        page_map = {}
        def ws_batch_handler(batch):
            page_map.update(batch)
            print(f"  Collected {len(page_map)} pages so far...")
        
        all_pages = wrapper.list_all_accessible_pages_batched(
            batch_size=500,
            batch_callback=ws_batch_handler
        )
        print(f"  Found {len(all_pages)} total workspace pages")
        
        # Find Exceptions page ID
        print(f"  Step 2/2: Removing Exceptions page and its descendants...")
        exceptions_id = None
        for pid, title in all_pages.items():
            if title == "Exceptions":
                exceptions_id = pid
                break
        
        if exceptions_id:
            # Remove Exceptions page
            removed_count = 0
            if exceptions_id in all_pages:
                del all_pages[exceptions_id]
                removed_count += 1
            
            # Find direct children of Exceptions by checking its blocks
            print(f"  Finding children of Exceptions page...")
            to_remove = set()
            try:
                blocks = wrapper.get_blocks(exceptions_id)
                for block in blocks:
                    block_type = block.get("type")
                    if block_type == "child_page":
                        to_remove.add(block["id"])
                    elif block_type == "child_database":
                        # Add database and all its rows
                        db_id = block["id"]
                        to_remove.add(db_id)
                        # Get all database rows (maintenance database entries)
                        try:
                            start_cursor = None
                            while True:
                                response = wrapper.client.databases.query(
                                    database_id=db_id,
                                    start_cursor=start_cursor,
                                    page_size=100
                                )
                                for page in response.get("results", []):
                                    to_remove.add(page["id"])
                                if not response.get("has_more"):
                                    break
                                start_cursor = response.get("next_cursor")
                        except Exception as e:
                            logger.warning(f"Failed to get database rows for {db_id}: {e}")
            except Exception as e:
                logger.warning(f"Failed to get Exceptions page children: {e}")
            
            # Remove all found descendants
            for pid in to_remove:
                if pid in all_pages:
                    del all_pages[pid]
                    removed_count += 1
            
            print(f"  Removed Exceptions page and {removed_count} descendants")
        
        page_map = all_pages
        print(f"  Final count: {len(page_map)} pages (after excluding Exceptions)")
        
        # Save to canonical
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                json.dump(page_map, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save canonical: {e}")
        logger.info(f"Found {len(page_map)} total pages (root subtree)")
        print(f"  Found {len(page_map)} pages (root subtree)")
    
    # Handle duplicates, blank titles, and maintenance pages
        try:
            # Define maintenance pages to exclude from canonical (used for tracking/debugging)
            MAINTENANCE_PAGE_TITLES = {
                "Exceptions",
                "DuplicatePageNames",
                "EvernoteLinkFailure",
                "UnresolvableEvernoteLinks",
                "Page-Title mention conversion failures",
            }
            
            title_to_ids = {}
            for pid, title in page_map.items():
                title_to_ids.setdefault(title, []).append(pid)
            
            # Find blank titles (None or empty string)
            blank_ids = title_to_ids.get(None, []) + title_to_ids.get("", [])
            
            # Find duplicate titles (more than 1 page with same title)
            duplicates = {t: ids for t, ids in title_to_ids.items() if t and len(ids) > 1}
            
            # Find maintenance pages to exclude
            maintenance_ids = []
            for title in MAINTENANCE_PAGE_TITLES:
                maintenance_ids.extend(title_to_ids.get(title, []))
            
            # Collect all page IDs to remove from canonical
            ids_to_remove = set(maintenance_ids)
            
            # Add blank titles to duplicates as "Blank-Page-Titles"
            if blank_ids:
                duplicates[None] = blank_ids  # Use None as key for blanks
                ids_to_remove.update(blank_ids)
            
            # Add all duplicate page IDs to removal set
            for ids in duplicates.values():
                ids_to_remove.update(ids)
            
            # Log maintenance page exclusions
            if maintenance_ids:
                print(f"  Excluding {len(maintenance_ids)} maintenance/exception pages from canonical")
                logger.info(f"Excluded {len(maintenance_ids)} maintenance pages: {MAINTENANCE_PAGE_TITLES}")
            
            # Log to DuplicatePageNames
            # TODO: Add --check-duplicates flag to run just duplicate detection without link resolution
            if duplicates:
                print(f"  Processing {len(duplicates)} duplicate/blank title groups...")
                tracker = ExceptionTracker(wrapper, root_id)
                tracker.track_duplicate_page_names(duplicates)
                total_removed = len(ids_to_remove)
                dup_count = total_removed - len(maintenance_ids)
                print(f"  Logged {len(duplicates)} duplicate/blank title groups to Exceptions → DuplicatePageNames")
                print(f"  Removing {total_removed} pages from canonical ({dup_count} duplicates/blanks + {len(maintenance_ids)} maintenance)")
                
                # Remove from page_map
                for pid in ids_to_remove:
                    page_map.pop(pid, None)
                
                logger.info(f"Removed {total_removed} pages from canonical: {dup_count} duplicates/blanks + {len(maintenance_ids)} maintenance")
            elif maintenance_ids:
                # Only maintenance pages to remove, no duplicates
                print(f"  Removing {len(maintenance_ids)} maintenance pages from canonical")
                for pid in ids_to_remove:
                    page_map.pop(pid, None)
                logger.info(f"Removed {len(maintenance_ids)} maintenance pages from canonical")
        except Exception as e:
            logger.warning(f"Failed to handle duplicates: {e}")
        
        # Save canonical collection to canonical.json (alphabetized by title)
        try:
            # Sort by title (case-insensitive)
            sorted_items = sorted(page_map.items(), key=lambda x: (x[1] or "").lower())
            sorted_page_map = dict(sorted_items)
            with open(canonical_path, "w", encoding="utf-8") as f:
                json.dump(sorted_page_map, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved canonical page list (alphabetized) to: {canonical_path}")
            print(f"  Saved canonical page list (alphabetized) to: {canonical_path}")
        except Exception as e:
            logger.error(f"Failed to save canonical page list: {e}")
            print(f"  Warning: Could not save canonical.json: {e}")
        
        # Initialize unfinished.json from canonical if not present
        if not unfinished_path.exists():
            try:
                with open(unfinished_path, "w", encoding="utf-8") as f:
                    # Preserve discovery order by writing items as an array
                    items = [{"id": pid, "title": title} for pid, title in page_map.items()]
                    json.dump(items, f, indent=2, ensure_ascii=False)
                print(f"  Initialized unfinished queue at: {unfinished_path}")
            except Exception as e:
                logger.error(f"Failed to initialize unfinished.json: {e}")
        
        # Touch completed.json if missing
        if not completed_path.exists():
            with open(completed_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            
    
    # stats.total_pages_scanned will be incremented as pages are processed
    
    # page_map is id->title now
    id_to_title = page_map
    
    # If --page-id specified, take precedence over all queue selection
    single_page_id = getattr(args, "page_id", None)
    if single_page_id:
        title = id_to_title.get(single_page_id, "")
        if not title:
            try:
                # Fallback: fetch title directly from Notion using robust extractor
                page_obj = wrapper.client.pages.retrieve(page_id=single_page_id)
                title = _extract_page_title(page_obj) or ""
                if title:
                    id_to_title[single_page_id] = title
            except Exception:
                title = title or ""
        page_map_to_scan = {single_page_id: title}
        logger.info(f"Scanning only page by ID: {single_page_id} ('{title}')")
        print(f"\n  Analyzing single page by ID: {single_page_id} ('{title}')")
    
    # If --page specified, filter to just that page
    single_page_name = getattr(args, "page", None)
    if single_page_name:
        # Build title->ids index
        title_to_ids = {}
        for pid, title in page_map.items():
            title_to_ids.setdefault(title, []).append(pid)
        if single_page_name not in title_to_ids:
            logger.error(f"Page '{single_page_name}' not found in page map")
            print(f"\nERROR: Page '{single_page_name}' not found.")
            print("\nAvailable pages (sample):")
            for title in sorted(set(title_to_ids.keys()))[:20]:
                print(f"  - {title}")
            if len(title_to_ids) > 20:
                print(f"  ... and {len(title_to_ids) - 20} more")
            return
        
        # Pick the first ID for this title
        single_page_id = title_to_ids[single_page_name][0]
        page_map_to_scan = {single_page_id: single_page_name}
        logger.info(f"Scanning only page: '{single_page_name}'")
        print(f"\n  Analyzing single page: '{single_page_name}'")
    
    # Default: scan all pages unless queue or filters override
    if not single_page_id and not single_page_name:
        page_map_to_scan = page_map
    
    # Optional: queue-based processing
    limit = getattr(args, "limit", None)
    
    # Load batch from unfinished.json if present (unless --page or --page-id is specified)
    if not getattr(args, "page", None) and not getattr(args, "page_id", None) and unfinished_path.exists():
        try:
            with open(unfinished_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if isinstance(items, list) and items:
                sel = items[: (limit if limit else len(items))]
                page_map_to_scan = {it["id"]: it.get("title", "") for it in sel}
                print(f"\nQueue mode: processing {len(page_map_to_scan)} page(s) from {unfinished_path}")
        except Exception as e:
            logger.warning(f"Failed to load unfinished.json: {e}. Falling back to scanning all pages in memory")
            page_map_to_scan = page_map
    
    # Step 2: Streaming scan+process with parallel workers
    logger.info("=" * 80)
    logger.info("STEP 2: Streaming scan and process for evernote:// links...")
    
    # Import helper functions at module level for streaming
    import os
    from enex2notion.link_resolver import _convert_text_with_all_links, _split_all_oversized_elements, normalize_page_blocks

    def _read_json_array(path: Path) -> list[dict]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _write_json_atomic(path: Path, data: list[dict]):
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

    # Normalization for robust matching (casefold, collapse whitespace, strip)
    def _norm(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00A0", " ")  # NBSP -> space
        s = " ".join(s.split())  # collapse whitespace
        return s.casefold()
    
    # Build normalized title→ids index once
    title_to_ids_ci = {}
    for pid, title in id_to_title.items():
        title_to_ids_ci.setdefault(_norm(title or ""), []).append((pid, title))

    # Determine if this is a full run (no --page or --page-id flag)
    is_full_run = not getattr(args, "page", None) and not getattr(args, "page_id", None)
    
    tracker = ExceptionTracker(wrapper, root_id)
    from enex2notion.review_tracker import ReviewTracker
    review = ReviewTracker(wrapper, root_id, recreate=is_full_run) if not args.dry_run else None
    
    # Track if we've already initialized exception pages for this run
    exception_pages_initialized = False
    
    # Helper to get parent page/database name
    def _get_import_source(page_id: str) -> str:
        """Retrieve the parent page or database name for a given page."""
        try:
            page_obj = wrapper.client.pages.retrieve(page_id=page_id)
            parent = page_obj.get("parent", {})
            parent_type = parent.get("type")
            if parent_type == "page_id":
                parent_id = parent.get("page_id")
                return id_to_title.get(parent_id, "Unknown Page")
            elif parent_type == "database_id":
                parent_id = parent.get("database_id")
                return id_to_title.get(parent_id, "Unknown Database")
            elif parent_type == "workspace":
                return "Workspace Root"
            return "Unknown"
        except Exception as e:
            logger.warning(f"Failed to get parent info for {page_id}: {e}")
            return "Unknown"

    # Initialize validation cache for target pages
    validation_cache = {}
    
    # Initialize cache for _get_import_source (must be before process_page)
    _get_import_source._cache = {}
    
    # Thread-safe stats
    stats_lock = Lock()
    exception_pages_initialized_lock = Lock()
    exception_pages_initialized_flag = [False]  # Mutable container for thread-safe flag
    
    # Streaming process_page function
    def process_page(page_id: str, page_title: str) -> dict[str, Any]:
        """Process a single page: scan for links, match, and update."""
        page_stats = {
            "links_found": 0,
            "links_matched": 0,
            "links_unmatched": 0,
            "links_ambiguous": 0,
            "links_invalid_target": 0,
            "blocks_updated": 0,
        }
        
        try:
            # Normalize oversized blocks before scanning
            normalize_page_blocks(wrapper, page_id)
            # Get blocks and scan
            blocks = wrapper.get_blocks(page_id)
            
            # Find evernote links
            link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
            page_stats["links_found"] = len(link_refs)
            
            if not link_refs:
                # No links, mark as completed immediately
                if not args.dry_run and not single_page_mode:
                    try:
                        cur_unf = _read_json_array(unfinished_path)
                        cur_unf = [it for it in cur_unf if it.get("id") != page_id]
                        _write_json_atomic(unfinished_path, cur_unf)
                        cur_comp = _read_json_array(completed_path)
                        cur_comp.append({"id": page_id, "title": page_title, "status": "No Links"})
                        _write_json_atomic(completed_path, cur_comp)
                    except Exception as e:
                        logger.warning(f"Queue update failed for {page_id}: {e}")
                return page_stats
            
            # Match links
            page_matched = []
            page_unmatched = []
            page_ambig = []
            page_invalid_target = []
            
            for link_ref in link_refs:
                key = _norm(link_ref.link_text or "")
                candidates = title_to_ids_ci.get(key, [])
                if len(candidates) == 1:
                    target_id = candidates[0][0]
                    if validate_target_page(wrapper, target_id, validation_cache):
                        page_matched.append((link_ref, target_id))
                    else:
                        page_invalid_target.append((link_ref, target_id, candidates[0][1]))
                elif len(candidates) > 1:
                    page_ambig.append((link_ref, candidates))
                else:
                    page_unmatched.append(link_ref)
            
            page_stats["links_matched"] = len(page_matched)
            page_stats["links_unmatched"] = len(page_unmatched)
            page_stats["links_ambiguous"] = len(page_ambig)
            page_stats["links_invalid_target"] = len(page_invalid_target)
            
            # Apply updates if not dry-run
            if not args.dry_run:
                # Build link lookup
                link_lookup = {}
                for ref, mid in page_matched:
                    link_lookup[_norm(ref.link_text or "")] = mid
                for ref, cands in page_ambig:
                    link_lookup[_norm(ref.link_text or "")] = None
                for ref in page_unmatched:
                    link_lookup[_norm(ref.link_text or "")] = None
                for ref, tgt_id, tgt_title in page_invalid_target:
                    link_lookup[_norm(ref.link_text or "")] = None
                
                # Group refs by (block_id, rich_text_index)
                refs_by_element = {}
                for ref in link_refs:
                    key = (ref.block_id, ref.rich_text_index)
                    refs_by_element.setdefault(key, []).append(ref)
                
                # Process each element
                for (block_id, rt_index), element_refs in refs_by_element.items():
                    try:
                        block = wrapper.get_block(block_id)
                        block_type = block.get("type")
                        
                        # Handle table_row blocks specially
                        if block_type == "table_row":
                            # Parse rt_index as "cell_idx:rt_idx"
                            if isinstance(rt_index, str) and ":" in rt_index:
                                cell_idx_str, cell_rt_idx_str = rt_index.split(":")
                                cell_idx = int(cell_idx_str)
                                cell_rt_idx = int(cell_rt_idx_str)
                                
                                cells = block.get("table_row", {}).get("cells", [])
                                if cell_idx >= len(cells):
                                    continue
                                
                                cell_rich_text = cells[cell_idx]
                                if cell_rt_idx >= len(cell_rich_text):
                                    continue
                                
                                element = cell_rich_text[cell_rt_idx]
                                if element.get("type") != "text":
                                    continue
                                
                                text_content = element.get("text", {}).get("content", "")
                                annotations = element.get("annotations", {})
                                
                                # Convert links
                                new_elements = _convert_text_with_all_links(text_content, annotations, link_lookup)
                                
                                # Update the cell
                                updated_cell_rich_text = cell_rich_text[:cell_rt_idx] + new_elements + cell_rich_text[cell_rt_idx+1:]
                                updated_cell_rich_text = _split_all_oversized_elements(updated_cell_rich_text)
                                
                                if len(updated_cell_rich_text) > 100:
                                    logger.warning(f"Table cell rich text too large: {len(updated_cell_rich_text)} elements")
                                    continue
                                
                                # Update the entire cells array
                                updated_cells = cells.copy()
                                updated_cells[cell_idx] = updated_cell_rich_text
                                wrapper.update_block(block_id, {"table_row": {"cells": updated_cells}})
                                page_stats["blocks_updated"] += 1
                            continue
                        
                        # Regular block handling
                        rich_text = block.get(block_type, {}).get("rich_text", [])
                        if not rich_text or rt_index >= len(rich_text):
                            continue
                        
                        element = rich_text[rt_index]
                        if element.get("type") != "text":
                            continue
                        
                        text_content = element.get("text", {}).get("content", "")
                        annotations = element.get("annotations", {})
                        
                        # Convert ALL links
                        new_elements = _convert_text_with_all_links(text_content, annotations, link_lookup)
                        
                        # Replace element
                        updated_rich_text = rich_text[:rt_index] + new_elements + rich_text[rt_index+1:]
                        
                        # Split oversized elements
                        updated_rich_text = _split_all_oversized_elements(updated_rich_text)
                        
                        # Check array size
                        if len(updated_rich_text) > 100:
                            error_msg = f"Rich text array too large ({len(updated_rich_text)} elements, max ~100)"
                            logger.warning(f"Block {block_id}: {error_msg}")
                            # Track failure
                            for ref in element_refs:
                                with exception_pages_initialized_lock:
                                    recreate = is_full_run and not exception_pages_initialized_flag[0]
                                    tracker.track_unmatched_link(
                                        page_title, page_id, ref.link_text, ref.original_url,
                                        block_id=ref.block_id, recreate=recreate
                                    )
                                    exception_pages_initialized_flag[0] = True
                            continue
                        
                        # Update block
                        wrapper.update_block(block_id, {block_type: {"rich_text": updated_rich_text}})
                        page_stats["blocks_updated"] += 1
                        
                        # Log review rows for unresolved/ambiguous links
                        if review:
                            # Get import source (with caching)
                            if page_id not in _get_import_source._cache:
                                _get_import_source._cache[page_id] = _get_import_source(page_id)
                            import_source = _get_import_source._cache[page_id]
                            
                            for ref in element_refs:
                                key = _norm(ref.link_text or "")
                                tgt_id = link_lookup.get(key)
                                
                                # Skip resolved links
                                if tgt_id:
                                    continue
                                
                                # Determine status
                                if any(r[0] == ref for r in page_ambig):
                                    status_row = "Ambiguous"
                                elif any(r[0] == ref for r in page_invalid_target):
                                    status_row = "Target Missing"
                                else:
                                    status_row = "Unresolved"
                                
                                review.log_link(
                                    link_text=ref.link_text,
                                    source_page_title=page_title,
                                    source_page_id=page_id,
                                    original_url=ref.original_url,
                                    status=status_row,
                                    import_source=import_source,
                                    source_block_id=ref.block_id,
                                    target_page_id=None,
                                    target_page_title=None,
                                )
                    
                    except Exception as e:
                        error_msg = str(e)
                        logger.warning(f"Failed updating block {block_id}: {error_msg}")
                        # Track failure
                        for ref in element_refs:
                            with exception_pages_initialized_lock:
                                recreate = is_full_run and not exception_pages_initialized_flag[0]
                                tracker.track_unmatched_link(
                                    page_title, page_id, ref.link_text,
                                    f"{ref.original_url} (Update failed: {error_msg[:100]})",
                                    block_id=ref.block_id, recreate=recreate
                                )
                                exception_pages_initialized_flag[0] = True
                
                # Update queue atomically
                if not single_page_mode:
                    matched_count = len(page_matched)
                    ambiguous_count = len(page_ambig)
                    unmatched_count = len(page_unmatched)
                    status = (
                        "Ambiguous" if ambiguous_count and not matched_count and not unmatched_count else
                        "Unresolved" if unmatched_count and not matched_count and not ambiguous_count else
                        "Partial" if ambiguous_count or unmatched_count else
                        "Resolved"
                    )
                    try:
                        cur_unf = _read_json_array(unfinished_path)
                        cur_unf = [it for it in cur_unf if it.get("id") != page_id]
                        _write_json_atomic(unfinished_path, cur_unf)
                        cur_comp = _read_json_array(completed_path)
                        cur_comp.append({"id": page_id, "title": page_title, "status": status})
                        _write_json_atomic(completed_path, cur_comp)
                    except Exception as e:
                        logger.warning(f"Queue update failed for {page_id}: {e}")
        
        except Exception as e:
            logger.error(f"Failed to process page {page_id}: {e}")
        
        return page_stats
    
    # Prepare pages to process list
    pages_to_process = list(page_map_to_scan.items())
    
    # Get workers count
    workers = getattr(args, 'workers', 3)
    print(f"\n✓ Processing {len(pages_to_process)} pages with {workers} worker(s)...\n")
    
    # Process pages (parallel or sequential)
    if workers > 1:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_page, pid, title): (pid, title) for pid, title in pages_to_process}
            
            with tqdm(total=len(pages_to_process), desc="Processing", unit="page") as pbar:
                for future in as_completed(futures):
                    page_stats = future.result()
                    with stats_lock:
                        stats.total_pages_scanned += 1
                        stats.total_links_found += page_stats["links_found"]
                        stats.links_matched += page_stats["links_matched"]
                        stats.links_unmatched += page_stats["links_unmatched"]
                        if page_stats["links_found"] > 0:
                            stats.pages_with_links += 1
                    pbar.update(1)
    else:
        # Sequential processing
        with tqdm(total=len(pages_to_process), desc="Processing", unit="page") as pbar:
            for pid, title in pages_to_process:
                page_stats = process_page(pid, title)
                stats.total_pages_scanned += 1
                stats.total_links_found += page_stats["links_found"]
                stats.links_matched += page_stats["links_matched"]
                stats.links_unmatched += page_stats["links_unmatched"]
                if page_stats["links_found"] > 0:
                    stats.pages_with_links += 1
                pbar.update(1)
    
    # Print summary
    print()
    print("=" * 80)
    print("LINK RESOLUTION SUMMARY")
    if args.dry_run:
        print("(DRY RUN - No changes made)")
    print("=" * 80)
    print(f"  Pages scanned:         {stats.total_pages_scanned}")
    print(f"  Pages with links:      {stats.pages_with_links}")
    print(f"  Links found:           {stats.total_links_found}")
    print(f"  Links matched:         {stats.links_matched}")
    print(f"  Links unmatched:       {stats.links_unmatched}")
    if stats.total_links_found > 0:
        success_rate = (stats.links_matched / stats.total_links_found) * 100
        print(f"  Success rate:          {success_rate:.1f}%")
    print("=" * 80)
    
    logger.info(f"Matched: {stats.links_matched}, Unmatched: {stats.links_unmatched}")
    logger.info("Link resolution complete")
