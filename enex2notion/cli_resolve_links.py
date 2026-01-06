"""CLI implementation for resolve-links command."""
import json
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
    format_notion_page_url,
    match_link_to_page,
    create_updated_rich_text,
)
from enex2notion.notion_api_wrapper import NotionAPIWrapper
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
    
    # Step 1: Collect all pages with batched inventory tracking
    logger.info("=" * 80)
    logger.info("STEP 1: Collecting all pages under root...")
    logger.info(f"Root page ID: {root_id}")
    
    # Check if we should load from/save to page list cache
    page_list_file = getattr(args, "page_list", None)
    
    if page_list_file and page_list_file.exists():
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
            # Use batched collection with inventory
            inventory = PageInventoryTracker(wrapper, root_id)
            inventory.create_inventory_page()
            inventory.update_status("Collecting pages...")
            
            def batch_handler(batch):
                inventory.append_page_batch(batch, set())
                inventory.update_status(f"Collecting pages... ({inventory.page_count} collected)")
            
            print("\nCollecting all imported pages (batches of 500)...")
            page_map = wrapper.list_all_pages_batched(root_id, batch_size=500, batch_callback=batch_handler)
    else:
        print("\nCollecting all imported pages (batches of 500)...")
        
        # Initialize inventory tracker
        inventory = PageInventoryTracker(wrapper, root_id)
        inventory.create_inventory_page()
        inventory.update_status("Collecting pages...")
        
        # Define batch handler to update inventory
        def batch_handler(batch):
            inventory.append_page_batch(batch, set())
            inventory.update_status(f"Collecting pages... ({inventory.page_count} collected)")
            print(f"  Collected {inventory.page_count} pages...")
        
        # Use batched collection
        page_map = wrapper.list_all_pages_batched(root_id, batch_size=500, batch_callback=batch_handler)
        
        logger.info(f"Found {len(page_map)} total pages")
        print(f"  Found {len(page_map)} pages")
        
        inventory.update_status(f"Collection complete - {len(page_map)} pages found")
        
        # Log duplicate page names, if any
        try:
            title_to_ids = {}
            for pid, title in page_map.items():
                title_to_ids.setdefault(title, []).append(pid)
            duplicates = {t: ids for t, ids in title_to_ids.items() if t is not None and len(ids) > 1}
            if duplicates:
                tracker = ExceptionTracker(wrapper, root_id)
                tracker.track_duplicate_page_names(duplicates)
                print(f"  Logged {len(duplicates)} duplicate title groups to Exceptions → DuplicatePageNames")
        except Exception as e:
            logger.warning(f"Failed to log duplicate titles: {e}")
        
        # Save to cache if requested
        if page_list_file:
            try:
                page_list_file.parent.mkdir(parents=True, exist_ok=True)
                with open(page_list_file, "w", encoding="utf-8") as f:
                    json.dump(page_map, f, indent=2, ensure_ascii=False)
                logger.info(f"Saved page list to: {page_list_file}")
                print(f"  Saved page list cache to: {page_list_file}")
            except Exception as e:
                logger.error(f"Failed to save page list cache: {e}")
                print(f"  Warning: Could not save cache file: {e}")
    
    stats.total_pages_scanned = len(page_map)
    
    # page_map is id->title now
    id_to_title = page_map
    
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
    else:
        # id->title mapping
        page_map_to_scan = page_map
    
    # Optional: queue-based processing
    queue_file = getattr(args, "queue", None)
    limit = getattr(args, "limit", None)
    
    if queue_file:
        # Ensure queue file exists; if not, create from page_map
        if not queue_file.exists():
            queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(queue_file, "w", encoding="utf-8") as qf:
                for pid, title in page_map.items():
                    qf.write(f"{pid}\t{title}\n")
            print(f"  Created queue: {queue_file}")
        
        # Load up to N page ids from queue
        with open(queue_file, "r", encoding="utf-8") as qf:
            lines = [ln.rstrip("\n") for ln in qf if ln.strip()]
        to_process = lines[: limit if limit else len(lines)]
        
        # Build map to scan from queue entries (id->title)
        page_map_to_scan = {}
        for ln in to_process:
            try:
                pid, title = ln.split("\t", 1)
                page_map_to_scan[pid] = title
            except Exception:
                continue
        
        print(f"\nQueue mode: processing {len(page_map_to_scan)} page(s) from {queue_file}")
    
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
                # Get all blocks from this page
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
    
    # Step 3: Match links to pages
    logger.info("=" * 80)
    logger.info("STEP 3: Matching links to pages...")
    
    print(f"\nMatching links to pages (mode: {args.match_mode})...")
    
    # Build case-insensitive title->ids index for matching
    title_to_ids_ci = {}
    for pid, title in id_to_title.items():
        key = (title or "").lower()
        title_to_ids_ci.setdefault(key, []).append((pid, title))
    
    ambiguous_links = []
    
    for link_ref in all_link_refs:
        key = (link_ref.link_text or "").lower()
        candidates = title_to_ids_ci.get(key, [])
        if len(candidates) == 1:
            matched_page_id, _matched_title = candidates[0]
            confidence = 1.0
        elif len(candidates) > 1:
            # Ambiguous: log and treat as unresolved (no change to content)
            ambiguous_links.append((link_ref, candidates))
            matched_page_id, confidence = (None, 0.0)
        else:
            # Fall back to fuzzy/existing matcher using first-id map
            title_to_id = {}
            for pid, title in id_to_title.items():
                t = title or ""
                if t and t not in title_to_id:
                    title_to_id[t] = pid
            matched_page_id, confidence = match_link_to_page(
                link_ref.link_text,
                title_to_id,
                args.match_mode
            )
        
        if matched_page_id:
            matched_page_title = id_to_title.get(matched_page_id, "")
            matched_links.append(MatchedLink(
                link_ref=link_ref,
                matched_page_id=matched_page_id,
                matched_page_title=matched_page_title,
                confidence=confidence
            ))
            stats.links_matched += 1
            
            logger.debug(f"Matched '{link_ref.link_text}' -> '{matched_page_title}' ({confidence:.0%})")
        else:
            unmatched_links.append(link_ref)
            stats.links_unmatched += 1
            
            logger.debug(f"No match found for '{link_ref.link_text}'")
    
    logger.info(f"Matched: {stats.links_matched}, Unmatched: {stats.links_unmatched}")
    print(f"  Matched: {stats.links_matched}")
    print(f"  Unmatched: {stats.links_unmatched}")
    
    # Step 4: Update links and mark unresolved (unless dry-run)
    if not args.dry_run and (stats.links_matched > 0 or stats.links_unmatched > 0):
        logger.info("=" * 80)
        logger.info("STEP 4: Updating links...")
        
        if stats.links_matched:
            print(f"\nUpdating {stats.links_matched} matched link(s)...")
        
        # Group matched links by block
        links_by_block = {}
        for match in matched_links:
            block_id = match.link_ref.block_id
            links_by_block.setdefault(block_id, []).append(match)
        
        # Group unmatched by block
        unresolved_by_block = {}
        for ref in unmatched_links:
            unresolved_by_block.setdefault(ref.block_id, []).append(ref)
        
        # Update blocks for matches
        with tqdm(total=len(links_by_block), desc="Updating", unit="block", ncols=80) as pbar:
            for block_id, block_matches in links_by_block.items():
                try:
                    block = wrapper.get_block(block_id)
                    block_type = block.get("type")
                    rich_text = block.get(block_type, {}).get("rich_text", [])
                    if not rich_text:
                        pbar.update(1)
                        continue
                    # Reverse apply to avoid index shifts
                    sorted_matches = sorted(block_matches, key=lambda m: m.link_ref.rich_text_index, reverse=True)
                    updated_rich_text = rich_text
                    for match in sorted_matches:
                        notion_url = format_notion_page_url(match.matched_page_id)
                        updated_rich_text = create_updated_rich_text(
                            updated_rich_text,
                            match.link_ref.rich_text_index,
                            match.link_ref.link_text,
                            notion_url
                        )
                    wrapper.update_block(block_id, {block_type: {"rich_text": updated_rich_text}})
                    stats.links_updated += len(block_matches)
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Failed to update block {block_id}: {e}")
                    pbar.update(1)
                    continue
        
        # Add ambiguous markers and log
        if ambiguous_links:
            tracker = ExceptionTracker(wrapper, root_id)
            print(f"\nLogging {len(ambiguous_links)} ambiguous link(s) to Exceptions and marking inline...")
            # Group by block for efficient updates
            ambiguous_by_block = {}
            for ref, candidates in ambiguous_links:
                ambiguous_by_block.setdefault(ref.block_id, []).append((ref, candidates))
            with tqdm(total=len(ambiguous_by_block), desc="Ambiguous", unit="block", ncols=80) as pbarA:
                for block_id, items in ambiguous_by_block.items():
                    try:
                        block = wrapper.get_block(block_id)
                        block_type = block.get("type")
                        rich_text = block.get(block_type, {}).get("rich_text", [])
                        if not rich_text:
                            pbarA.update(1)
                            continue
                        updated = list(rich_text)
                        for ref, candidates in sorted(items, key=lambda r: r[0].rich_text_index, reverse=True):
                            insert_index = min(ref.rich_text_index + 1, len(updated))
                            marker = {
                                "type": "text",
                                "text": {"content": " ⚠ ambiguous"},
                                "annotations": {"color": "orange"}
                            }
                            updated = updated[:insert_index] + [marker] + updated[insert_index:]
                            tracker.track_ambiguous_link(ref.page_title, ref.page_id, ref.link_text, candidates)
                        wrapper.update_block(block_id, {block_type: {"rich_text": updated}})
                        pbarA.update(1)
                    except Exception as e:
                        logger.warning(f"Failed to mark ambiguous in block {block_id}: {e}")
                        pbarA.update(1)
                        continue
        
        # Add unresolved markers and track exceptions
        if stats.links_unmatched:
            print(f"\nMarking {stats.links_unmatched} unmatched link(s) and logging exceptions...")
            tracker = tracker if 'tracker' in locals() else ExceptionTracker(wrapper, root_id)
            with tqdm(total=len(unresolved_by_block), desc="Marking", unit="block", ncols=80) as pbar2:
                for block_id, refs in unresolved_by_block.items():
                    try:
                        block = wrapper.get_block(block_id)
                        block_type = block.get("type")
                        rich_text = block.get(block_type, {}).get("rich_text", [])
                        if not rich_text:
                            pbar2.update(1)
                            continue
                        updated = list(rich_text)
                        # Insert markers after each unresolved item; reverse order
                        for ref in sorted(refs, key=lambda r: r.rich_text_index, reverse=True):
                            insert_index = min(ref.rich_text_index + 1, len(updated))
                            marker = {
                                "type": "text",
                                "text": {"content": " 🛑 unresolved"},
                                "annotations": {"color": "red"}
                            }
                            updated = updated[:insert_index] + [marker] + updated[insert_index:]
                            # Log exception entry
                            tracker.track_unmatched_link(ref.page_title, ref.page_id, ref.link_text, ref.original_url)
                        wrapper.update_block(block_id, {block_type: {"rich_text": updated}})
                        pbar2.update(1)
                    except Exception as e:
                        logger.warning(f"Failed to mark unresolved in block {block_id}: {e}")
                        pbar2.update(1)
                        continue
        
        logger.info(f"Successfully updated {stats.links_updated} link(s)")
        print(f"\n  Successfully updated {stats.links_updated} link(s)")
    
    # Step 5: Generate report
    logger.info("=" * 80)
    logger.info("STEP 5: Generating report...")
    
    print_resolution_report(
        stats,
        matched_links,
        unmatched_links,
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    # Queue maintenance: remove processed pages and log outcomes
    if queue_file and not args.dry_run:
        processed_ids = set(page_map_to_scan.values())
        try:
            with open(queue_file, "r", encoding="utf-8") as qf:
                lines = [ln.rstrip("\n") for ln in qf if ln.strip()]
            remaining = []
            finished_path = queue_file.parent / "finished.list"
            failed_path = queue_file.parent / "failed.list"
            # Determine per-page status: if any unmatched refs in that page, still consider finished (we kept the link+marker)
            # Fail only if scanning/updating the page raised an exception (not tracked here); so we move all processed to finished.
            processed_lines = set()
            for pid, title in page_map_to_scan.items():
                processed_lines.add(f"{pid}\t{title}")
            for ln in lines:
                if ln in processed_lines:
                    # Move to finished
                    with open(finished_path, "a", encoding="utf-8") as fp:
                        fp.write(ln + "\n")
                else:
                    remaining.append(ln)
            with open(queue_file, "w", encoding="utf-8") as qf:
                qf.write("\n".join(remaining) + ("\n" if remaining else ""))
            print(f"\nQueue updated: {len(processed_lines)} moved to finished, {len(remaining)} remaining")
        except Exception as e:
            logger.warning(f"Queue maintenance failed: {e}")
    
    if args.summary:
        save_resolution_report(
            stats,
            matched_links,
            unmatched_links,
            args.summary,
            dry_run=args.dry_run
        )
    
    logger.info("Link resolution complete")
