"""CLI implementation for retry-failed-links command."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

from tqdm import tqdm

from enex2notion.link_resolver import (
    find_evernote_links_in_page,
    _convert_text_with_all_links,
    _split_all_oversized_elements,
    validate_target_page,
)
from enex2notion.notion_api_wrapper import NotionAPIWrapper
from enex2notion.review_tracker import ReviewTracker

logger = logging.getLogger(__name__)


def retry_failed_links_command(wrapper: NotionAPIWrapper, root_id: str, args):
    """Retry failed evernote:// link conversions with enhanced capabilities.
    
    Features:
    - Multi-link support: Processes ALL links in a single rich_text element
    - ReviewTracker integration: Logs all conversion attempts to database
    - Database page support: Works with both regular and database pages
    - Parallel processing: Processes multiple pages concurrently
    - Link validation: Verifies target pages exist before conversion
    
    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Root page ID
        args: Parsed command-line arguments
    """
    logger.info("=" * 80)
    logger.info("RETRY FAILED LINKS COMMAND")
    logger.info("=" * 80)
    
    # Determine queue directory
    queue_dir = getattr(args, "queue_directory", None)
    if not queue_dir:
        queue_dir = Path.home() / "Downloads" / "imports"
    
    canonical_path = queue_dir / "canonical.json"
    
    if not canonical_path.exists():
        print(f"\n✗ ERROR: canonical.json not found at {canonical_path}")
        print("  Run --resolve-links first to create the canonical.")
        return
    
    # Load canonical
    print(f"\nLoading canonical from {canonical_path}")
    with open(canonical_path) as f:
        page_map = json.load(f)
    
    print(f"✓ Loaded {len(page_map)} pages")
    
    # Build normalized title lookup (case-insensitive, collapsed whitespace)
    normalized_map = {}
    for page_id, title in page_map.items():
        if title:
            # Normalize: lowercase, collapse whitespace, replace nbsp
            normalized = re.sub(r'\s+', ' ', title.replace('\xa0', ' ')).strip().lower()
            if normalized not in normalized_map:
                normalized_map[normalized] = []
            normalized_map[normalized].append((page_id, title))
    
    print(f"✓ Built normalized lookup with {len(normalized_map)} unique titles")
    
    # Initialize ReviewTracker for logging
    print("\n✓ Initializing ReviewTracker database...")
    is_full_run = not getattr(args, "page", None) and not getattr(args, "page_id", None)
    review = ReviewTracker(wrapper, root_id, recreate=is_full_run) if not args.dry_run else None
    if review:
        print(f"  Database: {review.ensure_db()}")
    
    # Determine pages to process
    page_name = getattr(args, 'page', None)
    page_id_arg = getattr(args, 'page_id', None)
    limit = getattr(args, 'limit', None)
    
    pages_to_process = []
    
    if page_id_arg:
        # Single page by ID
        pages_to_process = [(page_id_arg, page_map.get(page_id_arg, "Unknown"))]
    elif page_name:
        # Single page by name
        results = wrapper.search_pages(page_name)
        if not results:
            print(f"\n✗ Page not found: '{page_name}'")
            return
        page_id = results[0]['id']
        title = _extract_title_from_page(results[0], page_name)
        pages_to_process = [(page_id, title)]
    else:
        # Process from queue or all pages
        unfinished_path = queue_dir / "unfinished.json"
        if unfinished_path.exists():
            with open(unfinished_path) as f:
                items = json.load(f)
            if isinstance(items, list) and items:
                sel = items[:(limit if limit else len(items))]
                pages_to_process = [(it["id"], it.get("title", "")) for it in sel]
                print(f"\n✓ Loaded {len(pages_to_process)} pages from queue")
        else:
            # Use all pages from canonical
            pages_to_process = list(page_map.items())
            if limit:
                pages_to_process = pages_to_process[:limit]
            print(f"\n✓ Processing {len(pages_to_process)} pages from canonical")
    
    if not pages_to_process:
        print("\n✗ No pages to process")
        return
    
    # Get normalization function (same as resolve-links)
    def _norm(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u00A0", " ")  # NBSP -> space
        s = " ".join(s.split())  # collapse whitespace
        return s.casefold()
    
    # Build normalized lookup for matching
    title_to_ids_ci = {}
    for pid, title in page_map.items():
        title_to_ids_ci.setdefault(_norm(title or ""), []).append((pid, title))
    
    # Initialize validation cache
    validation_cache = {}
    
    # Thread-safe stats
    stats_lock = Lock()
    stats = {
        "pages_processed": 0,
        "links_found": 0,
        "links_resolved": 0,
        "links_failed": 0,
        "blocks_updated": 0,
    }
    
    # Process a single page
    def process_page(page_id: str, page_title: str) -> dict[str, Any]:
        """Process a single page for link resolution."""
        page_stats = {
            "links_found": 0,
            "links_resolved": 0,
            "links_failed": 0,
            "blocks_updated": 0,
        }
        
        try:
            # Get all blocks (works for both regular and database pages)
            blocks = wrapper.get_blocks(page_id)
            
            # Find evernote links using the standard function
            link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
            page_stats["links_found"] = len(link_refs)
            
            if not link_refs:
                return page_stats
            
            # Build link lookup for this page
            link_lookup = {}
            matched_refs = []
            unmatched_refs = []
            ambiguous_refs = []
            invalid_target_refs = []
            
            for ref in link_refs:
                key = _norm(ref.link_text or "")
                candidates = title_to_ids_ci.get(key, [])
                
                if len(candidates) == 1:
                    target_id = candidates[0][0]
                    # Validate target page
                    if validate_target_page(wrapper, target_id, validation_cache):
                        link_lookup[key] = target_id
                        matched_refs.append((ref, target_id, candidates[0][1]))
                    else:
                        link_lookup[key] = None
                        invalid_target_refs.append((ref, target_id, candidates[0][1]))
                elif len(candidates) > 1:
                    link_lookup[key] = None
                    ambiguous_refs.append((ref, candidates))
                else:
                    link_lookup[key] = None
                    unmatched_refs.append(ref)
            
            # Group refs by block and rich_text_index
            refs_by_element = {}
            for ref in link_refs:
                key = (ref.block_id, ref.rich_text_index)
                refs_by_element.setdefault(key, []).append(ref)
            
            # Process each element with links
            for (block_id, rt_index), element_refs in refs_by_element.items():
                try:
                    block = wrapper.get_block(block_id)
                    block_type = block.get("type")
                    rich_text = block.get(block_type, {}).get("rich_text", [])
                    
                    if not rich_text or rt_index >= len(rich_text):
                        continue
                    
                    element = rich_text[rt_index]
                    if element.get("type") != "text":
                        continue
                    
                    text_content = element.get("text", {}).get("content", "")
                    annotations = element.get("annotations", {})
                    
                    # Convert ALL links using recursive function
                    new_elements = _convert_text_with_all_links(text_content, annotations, link_lookup)
                    
                    # Replace element with new elements
                    updated_rich_text = rich_text[:rt_index] + new_elements + rich_text[rt_index+1:]
                    
                    # Final validation
                    updated_rich_text = _split_all_oversized_elements(updated_rich_text)
                    
                    # Check array size
                    if len(updated_rich_text) > 100:
                        logger.warning(f"Rich text array too large for block {block_id}: {len(updated_rich_text)} elements")
                        page_stats["links_failed"] += len(element_refs)
                        continue
                    
                    # Update block
                    if not args.dry_run:
                        wrapper.update_block(block_id, {block_type: {"rich_text": updated_rich_text}})
                        page_stats["blocks_updated"] += 1
                    
                    # Count resolved links in this element
                    resolved_count = sum(1 for ref in element_refs if link_lookup.get(_norm(ref.link_text or "")))
                    failed_count = len(element_refs) - resolved_count
                    page_stats["links_resolved"] += resolved_count
                    page_stats["links_failed"] += failed_count
                    
                except Exception as e:
                    logger.warning(f"Failed to process block {block_id}: {e}")
                    page_stats["links_failed"] += len(element_refs)
            
            # Log to ReviewTracker (only failed links)
            if review and not args.dry_run:
                # Get import source
                import_source = _get_import_source(wrapper, page_id, page_map)
                
                # Log unresolved/ambiguous/invalid links
                for ref in unmatched_refs:
                    review.log_link(
                        link_text=ref.link_text,
                        source_page_title=page_title,
                        source_page_id=page_id,
                        original_url=ref.original_url,
                        status="Unresolved",
                        import_source=import_source,
                        source_block_id=ref.block_id,
                    )
                
                for ref, candidates in ambiguous_refs:
                    review.log_link(
                        link_text=ref.link_text,
                        source_page_title=page_title,
                        source_page_id=page_id,
                        original_url=ref.original_url,
                        status="Ambiguous",
                        import_source=import_source,
                        source_block_id=ref.block_id,
                    )
                
                for ref, tgt_id, tgt_title in invalid_target_refs:
                    review.log_link(
                        link_text=ref.link_text,
                        source_page_title=page_title,
                        source_page_id=page_id,
                        original_url=ref.original_url,
                        status="Target Missing",
                        import_source=import_source,
                        source_block_id=ref.block_id,
                        target_page_id=tgt_id,
                        target_page_title=tgt_title,
                    )
        
        except Exception as e:
            logger.error(f"Failed to process page {page_id}: {e}")
        
        return page_stats
    
    # Process pages (parallel or sequential)
    workers = getattr(args, 'workers', 4)
    print(f"\n✓ Processing {len(pages_to_process)} pages with {workers} workers...\n")
    
    if workers > 1:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_page, pid, title): (pid, title) for pid, title in pages_to_process}
            
            with tqdm(total=len(pages_to_process), desc="Processing", unit="page") as pbar:
                for future in as_completed(futures):
                    page_stats = future.result()
                    with stats_lock:
                        stats["pages_processed"] += 1
                        stats["links_found"] += page_stats["links_found"]
                        stats["links_resolved"] += page_stats["links_resolved"]
                        stats["links_failed"] += page_stats["links_failed"]
                        stats["blocks_updated"] += page_stats["blocks_updated"]
                    pbar.update(1)
    else:
        # Sequential processing
        with tqdm(total=len(pages_to_process), desc="Processing", unit="page") as pbar:
            for pid, title in pages_to_process:
                page_stats = process_page(pid, title)
                stats["pages_processed"] += 1
                stats["links_found"] += page_stats["links_found"]
                stats["links_resolved"] += page_stats["links_resolved"]
                stats["links_failed"] += page_stats["links_failed"]
                stats["blocks_updated"] += page_stats["blocks_updated"]
                pbar.update(1)
    
    # Print summary
    print()
    print("=" * 80)
    print("RETRY FAILED LINKS SUMMARY")
    if args.dry_run:
        print("(DRY RUN - No changes made)")
    print("=" * 80)
    print(f"  Pages processed:       {stats['pages_processed']}")
    print(f"  Blocks updated:        {stats['blocks_updated']}")
    print(f"  Links found:           {stats['links_found']}")
    print(f"  Links resolved:        {stats['links_resolved']}")
    print(f"  Links failed:          {stats['links_failed']}")
    if stats['links_found'] > 0:
        success_rate = (stats['links_resolved'] / stats['links_found']) * 100
        print(f"  Success rate:          {success_rate:.1f}%")
    print("=" * 80)


def _extract_title_from_page(page_obj: dict[str, Any], fallback: str) -> str:
    """Extract title from page object."""
    props = page_obj.get('properties', {})
    for key, value in props.items():
        if value.get('type') == 'title':
            title_content = value.get('title', [])
            if title_content:
                return title_content[0].get('plain_text', fallback)
    return fallback


def _get_import_source(wrapper: NotionAPIWrapper, page_id: str, id_to_title: dict[str, str]) -> str:
    """Get the import source (parent page/database) for a page."""
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
    except Exception:
        return "Unknown"
