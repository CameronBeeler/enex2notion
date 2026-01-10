"""CLI implementation for resolve-links command."""
import json
from pathlib import Path
import logging
from pathlib import Path

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
    
    # If canonical exists and we're in single-page mode, just load it (assume already clean)
    if single_page_mode and canonical_path.exists():
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
        if queue_dir:
            print("\nCollecting all accessible workspace pages (batches of 500)...")
            page_map = {}
            def ws_batch_handler(batch):
                # batch is id->title; merge and persist incrementally
                page_map.update(batch)
                try:
                    with open(canonical_path, "w", encoding="utf-8") as f:
                        json.dump(page_map, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                print(f"  Collected {len(page_map)} pages so far...")
            page_map = wrapper.list_all_accessible_pages_batched(batch_size=500, batch_callback=ws_batch_handler)
            logger.info(f"Found {len(page_map)} total pages (workspace)")
            print(f"  Found {len(page_map)} pages (workspace)")
        else:
            print("\nCollecting all pages (root subtree + workspace) in batches of 500...")
            # Initialize inventory tracker
            inventory = PageInventoryTracker(wrapper, root_id)
            inventory.create_inventory_page()
            inventory.update_status("Collecting pages...")
            
            # Define batch handler to update inventory
            def batch_handler(batch):
                inventory.append_page_batch(batch, set())
                inventory.update_status(f"Collecting pages... ({inventory.page_count} collected)")
                print(f"  Collected {inventory.page_count} pages...")
            
            # Use batched collection for root subtree
            pages_root = wrapper.list_all_pages_batched(root_id, batch_size=500, batch_callback=batch_handler)
            # Workspace-wide (no callback to avoid excessive writes)
            pages_ws = wrapper.list_all_accessible_pages_batched(batch_size=500)
            # Merge id->title
            page_map = pages_root.copy(); page_map.update(pages_ws)
            
            logger.info(f"Found {len(page_map)} total pages (merged)")
            print(f"  Found {len(page_map)} pages (merged)")
            
            inventory.update_status(f"Collection complete - {len(page_map)} pages found")
        
        # Handle duplicates and blank titles
        try:
            title_to_ids = {}
            for pid, title in page_map.items():
                title_to_ids.setdefault(title, []).append(pid)
            
            # Find blank titles (None or empty string)
            blank_ids = title_to_ids.get(None, []) + title_to_ids.get("", [])
            
            # Find duplicate titles (more than 1 page with same title)
            duplicates = {t: ids for t, ids in title_to_ids.items() if t and len(ids) > 1}
            
            # Collect all page IDs to remove from canonical
            ids_to_remove = set()
            
            # Add blank titles to duplicates as "Blank-Page-Titles"
            if blank_ids:
                duplicates[None] = blank_ids  # Use None as key for blanks
                ids_to_remove.update(blank_ids)
            
            # Add all duplicate page IDs to removal set
            for ids in duplicates.values():
                ids_to_remove.update(ids)
            
            # Log to DuplicatePageNames
            # TODO: Add --check-duplicates flag to run just duplicate detection without link resolution
            if duplicates:
                print(f"  Processing {len(duplicates)} duplicate/blank title groups...")
                tracker = ExceptionTracker(wrapper, root_id)
                tracker.track_duplicate_page_names(duplicates)
                total_removed = len(ids_to_remove)
                print(f"  Logged {len(duplicates)} duplicate/blank title groups to Exceptions → DuplicatePageNames")
                print(f"  Removing {total_removed} pages from canonical (all duplicates + blanks)")
                
                # Remove from page_map
                for pid in ids_to_remove:
                    page_map.pop(pid, None)
                
                logger.info(f"Removed {total_removed} duplicate/blank pages from canonical")
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
            
    
    stats.total_pages_scanned = len(page_map)
    
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
    
    # Step 2: Scan for evernote:// links
    logger.info("=" * 80)
    logger.info("STEP 2: Scanning pages for evernote:// links...")
    
    print("\nScanning pages for evernote:// links...")
    
    pages_with_links_set = set()
    all_link_refs = []
    
    # Use progress bar for scanning
    with tqdm(total=len(page_map_to_scan), desc="Scanning", unit="page", ncols=80) as pbar:
        for page_id, page_title in page_map_to_scan.items():
            try:
                # Pass 0: normalize oversized blocks before scanning
                from enex2notion.link_resolver import normalize_page_blocks
                normalize_page_blocks(wrapper, page_id)
                # Now get blocks and scan
                blocks = wrapper.get_blocks(page_id)
                
                # Find evernote links in the page
                link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
                
                if link_refs:
                    pages_with_links_set.add(page_id)
                    all_link_refs.extend(link_refs)
                
                pbar.update(1)
                
            except Exception as e:
                logger.warning(f"Failed to scan page '{page_title}' ({page_id}): {e}")
                pbar.update(1)
                continue
    
    stats.pages_with_links = len(pages_with_links_set)
    stats.total_links_found = len(all_link_refs)
    
    logger.info(f"Found {stats.total_links_found} evernote:// links in {stats.pages_with_links} pages")
    print(f"\n  Found {stats.total_links_found} evernote:// links in {stats.pages_with_links} pages")
    
    if stats.total_links_found == 0:
        print("\nNo evernote:// links found. Nothing to resolve.")
        return
    
    # Step 3–4: Per-page processing with atomic queue updates
    import os, tempfile

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

    # Group found link refs by page
    refs_by_page = {}
    for ref in all_link_refs:
        refs_by_page.setdefault(ref.page_id, []).append(ref)

    for page_id, page_title in page_map_to_scan.items():
        page_refs = refs_by_page.get(page_id, [])
        page_matched = []
        page_unmatched = []
        page_ambig = []
        # Match within this page
        for link_ref in page_refs:
            key = _norm(link_ref.link_text or "")
            candidates = title_to_ids_ci.get(key, [])
            if len(candidates) == 1:
                page_matched.append((link_ref, candidates[0][0]))
            elif len(candidates) > 1:
                page_ambig.append((link_ref, candidates))
            else:
                # No exact match found
                page_unmatched.append(link_ref)
        # Apply updates for this page (unless dry-run)
        if not args.dry_run:
            # Build link lookup dict: link_text (lowercase) -> target_page_id
            from enex2notion.link_resolver import _convert_text_with_all_links
            link_lookup = {}
            for ref, mid in page_matched:
                link_lookup[_norm(ref.link_text or "")] = mid
            # Ambiguous and unmatched get None
            for ref, cands in page_ambig:
                link_lookup[_norm(ref.link_text or "")] = None
            for ref in page_unmatched:
                link_lookup[_norm(ref.link_text or "")] = None
            
            # Group ALL page refs by (block_id, rich_text_index)
            refs_by_element = {}
            for ref in page_refs:
                key = (ref.block_id, ref.rich_text_index)
                refs_by_element.setdefault(key, []).append(ref)
            
            # Process each element that has links
            for (block_id, rt_index), element_refs in refs_by_element.items():
                try:
                    block = wrapper.get_block(block_id)
                    block_type = block.get("type")
                    rich_text = block.get(block_type, {}).get("rich_text", [])
                    if not rich_text or rt_index >= len(rich_text):
                        continue
                    
                    # Get the element to process
                    element = rich_text[rt_index]
                    if element.get("type") != "text":
                        continue
                    
                    text_content = element.get("text", {}).get("content", "")
                    annotations = element.get("annotations", {})
                    
                    # Check if source element is already oversized (shouldn't happen, but detect it)
                    if len(text_content) > 2000:
                        logger.warning(f"Source element already exceeds 2000 chars ({len(text_content)} chars) - Notion API issue")
                    
                    # Convert ALL links in this element using recursive function
                    new_elements = _convert_text_with_all_links(text_content, annotations, link_lookup)
                    
                    
                    # Replace this element with the new elements
                    updated_rich_text = rich_text[:rt_index] + new_elements + rich_text[rt_index+1:]
                    
                    # Final validation: split any oversized text elements
                    from enex2notion.link_resolver import _split_all_oversized_elements
                    updated_rich_text = _split_all_oversized_elements(updated_rich_text)
                    
                    # Check if rich_text array is too large (Notion limit: ~100 elements)
                    if len(updated_rich_text) > 100:
                        error_msg = f"Rich text array too large ({len(updated_rich_text)} elements, max ~100)"
                        logger.warning(f"Block {block_id}: {error_msg}")
                        # Track this failure
                        for ref in element_refs:
                            recreate = is_full_run and not exception_pages_initialized
                            tracker.track_unmatched_link(
                                page_title, page_id, ref.link_text, ref.original_url, 
                                block_id=ref.block_id, recreate=recreate
                            )
                            exception_pages_initialized = True
                        continue
                    
                    wrapper.update_block(block_id, {block_type: {"rich_text": updated_rich_text}})
                    stats.links_updated += len(element_refs)
                    
                    # Log review rows ONLY for unresolved/ambiguous links
                    if review:
                        # Get import source once per page (cached)
                        if not hasattr(_get_import_source, '_cache'):
                            _get_import_source._cache = {}
                        if page_id not in _get_import_source._cache:
                            _get_import_source._cache[page_id] = _get_import_source(page_id)
                        import_source = _get_import_source._cache[page_id]
                        
                        for ref in element_refs:
                            # Determine status per ref (matched/ambiguous/unmatched)
                            key = _norm(ref.link_text or "")
                            tgt_id = link_lookup.get(key)
                            
                            # Skip resolved links - only log unresolved/ambiguous
                            if tgt_id:  # Successfully resolved
                                continue
                            
                            # Determine if ambiguous or unresolved
                            status_row = "Ambiguous" if any(r[0] == ref for r in [*page_ambig]) else "Unresolved"
                            
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
                    logger.warning(f"Failed updating block {block_id} element {rt_index}: {error_msg}")
                    # Track failures in exception page
                    for ref in element_refs:
                        recreate = is_full_run and not exception_pages_initialized
                        tracker.track_unmatched_link(
                            page_title, page_id, ref.link_text, 
                            f"{ref.original_url} (Update failed: {error_msg[:100]})", 
                            block_id=ref.block_id, recreate=recreate
                        )
                        exception_pages_initialized = True
            
            # NOTE: Unmatched and ambiguous links are now tracked in the review database
            # ("Page-Title mention conversion failures") with full details including ImportSource.
            # The old EvernoteLinkFailure and UnresolvableEvernoteLinks pages are no longer populated
            # for normal unmatched/ambiguous links. They are only used for technical failures
            # (e.g., block update failures, rich_text array too large).
            
            # Compute page status for queue update
            matched_count = len(page_matched)
            ambiguous_count = len(page_ambig)
            unmatched_count = len(page_unmatched)
            status = (
                "Ambiguous" if ambiguous_count and not matched_count and not unmatched_count else
                "Unresolved" if unmatched_count and not matched_count and not ambiguous_count else
                "Partial" if ambiguous_count or unmatched_count else
                "Resolved"
            )
            # Atomic JSON updates
            try:
                # Remove from unfinished
                cur_unf = _read_json_array(unfinished_path)
                cur_unf = [it for it in cur_unf if it.get("id") != page_id]
                _write_json_atomic(unfinished_path, cur_unf)
                # Append to completed
                cur_comp = _read_json_array(completed_path)
                cur_comp.append({"id": page_id, "title": page_title, "status": status})
                _write_json_atomic(completed_path, cur_comp)
                print(f"queued → completed: {page_title} ({page_id}) [status={status}]")
            except Exception as e:
                logger.warning(f"Queue update failed for {page_id}: {e}")
        # Update global stats
        stats.links_matched += len(page_matched)
        stats.links_unmatched += len(page_unmatched)

    logger.info(f"Matched: {stats.links_matched}, Unmatched: {stats.links_unmatched}")
    print(f"  Matched: {stats.links_matched}")
    print(f"  Unmatched: {stats.links_unmatched}")
    logger.info("Link resolution complete")
