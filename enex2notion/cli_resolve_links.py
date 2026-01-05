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
    
    # Step 1: Collect all pages (or load from cache)
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
            page_map = wrapper.list_all_pages_recursive(root_id)
    else:
        print("\nCollecting all imported pages...")
        page_map = wrapper.list_all_pages_recursive(root_id)
        logger.info(f"Found {len(page_map)} total pages")
        print(f"  Found {len(page_map)} pages")
        
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
    
    # Create reverse map for looking up page titles from IDs
    id_to_title = {page_id: title for title, page_id in page_map.items()}
    
    # If --page specified, filter to just that page
    single_page_name = getattr(args, "page", None)
    if single_page_name:
        if single_page_name not in page_map:
            logger.error(f"Page '{single_page_name}' not found in page map")
            print(f"\nERROR: Page '{single_page_name}' not found.")
            print("\nAvailable pages:")
            for title in sorted(page_map.keys())[:20]:
                print(f"  - {title}")
            if len(page_map) > 20:
                print(f"  ... and {len(page_map) - 20} more")
            return
        
        # Filter to just this page
        single_page_id = page_map[single_page_name]
        page_map_to_scan = {single_page_name: single_page_id}
        logger.info(f"Scanning only page: '{single_page_name}'")
        print(f"\n  Analyzing single page: '{single_page_name}'")
    else:
        page_map_to_scan = page_map
    
    # Step 2: Scan for evernote:// links
    logger.info("=" * 80)
    logger.info("STEP 2: Scanning pages for evernote:// links...")
    
    print("\nScanning pages for evernote:// links...")
    
    pages_with_links_set = set()
    all_link_refs = []
    
    # Use progress bar for scanning
    with tqdm(total=len(page_map_to_scan), desc="Scanning", unit="page", ncols=80) as pbar:
        for page_title, page_id in page_map_to_scan.items():
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
    
    for link_ref in all_link_refs:
        matched_page_id, confidence = match_link_to_page(
            link_ref.link_text,
            page_map,
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
    
    # Step 4: Update links (unless dry-run)
    if not args.dry_run and stats.links_matched > 0:
        logger.info("=" * 80)
        logger.info("STEP 4: Updating links...")
        
        print(f"\nUpdating {stats.links_matched} link(s)...")
        
        # Group links by block_id to batch updates
        links_by_block = {}
        for match in matched_links:
            block_id = match.link_ref.block_id
            if block_id not in links_by_block:
                links_by_block[block_id] = []
            links_by_block[block_id].append(match)
        
        # Update blocks
        with tqdm(total=len(links_by_block), desc="Updating", unit="block", ncols=80) as pbar:
            for block_id, block_matches in links_by_block.items():
                try:
                    # Get the current block
                    block = wrapper.get_block(block_id)
                    block_type = block.get("type")
                    
                    # Get the rich_text array
                    rich_text = block.get(block_type, {}).get("rich_text", [])
                    
                    if not rich_text:
                        logger.warning(f"Block {block_id} has no rich_text")
                        pbar.update(1)
                        continue
                    
                    # Apply all updates to this block's rich_text
                    # Sort by rich_text_index in reverse to avoid index shifting issues
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
                    
                    # Update the block
                    wrapper.update_block(block_id, {
                        block_type: {"rich_text": updated_rich_text}
                    })
                    
                    stats.links_updated += len(block_matches)
                    logger.debug(f"Updated {len(block_matches)} link(s) in block {block_id}")
                    
                    pbar.update(1)
                    
                except Exception as e:
                    logger.error(f"Failed to update block {block_id}: {e}")
                    pbar.update(1)
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
    
    if args.summary:
        save_resolution_report(
            stats,
            matched_links,
            unmatched_links,
            args.summary,
            dry_run=args.dry_run
        )
    
    logger.info("Link resolution complete")
