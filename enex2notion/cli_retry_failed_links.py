"""CLI implementation for retry-failed-links command."""
import json
import logging
import re
from pathlib import Path

from enex2notion.link_resolver import find_evernote_links_in_page, create_updated_rich_text
from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)


def retry_failed_links_command(wrapper: NotionAPIWrapper, root_id: str, args):
    """Retry failed evernote:// link conversions and remove 🛑 markers.
    
    Scans all pages for unconverted markdown links, attempts to resolve them
    using the canonical.json, and removes 🛑 markers if successful.
    
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
    
    # TODO: Get list of pages to scan (either from --page or all pages)
    # For now, let user specify a page
    page_name = getattr(args, 'page', None)
    page_id_arg = getattr(args, 'page_id', None)
    
    if not page_name and not page_id_arg:
        print("\n✗ ERROR: Must specify --page NAME or --page-id ID")
        print("  Example: --retry-failed-links --page 'Employment & Technology Stories'")
        return
    
    # Find the page
    if page_id_arg:
        target_page_id = page_id_arg
        target_page_title = "Unknown"
    else:
        # Search for page
        results = wrapper.search_pages(page_name)
        if not results:
            print(f"\n✗ Page not found: '{page_name}'")
            return
        target_page_id = results[0]['id']
        # Get title from properties
        props = results[0].get('properties', {})
        for key, value in props.items():
            if value.get('type') == 'title':
                title_content = value.get('title', [])
                if title_content:
                    target_page_title = title_content[0].get('plain_text', page_name)
                    break
        else:
            target_page_title = page_name
    
    print(f"\n✓ Processing page: {target_page_title}")
    print(f"  ID: {target_page_id}")
    
    # Get all blocks
    print("  Fetching blocks...")
    blocks = wrapper.get_blocks(target_page_id)
    print(f"  ✓ Found {len(blocks)} blocks")
    
    # Find markdown links
    print("  Scanning for markdown links...")
    markdown_pattern = r'\[([^\]]+)\]\((evernote[^\)]+)\)'
    
    updates_made = 0
    links_found = 0
    
    for block in blocks:
        block_type = block.get('type')
        if block_type not in ['paragraph', 'bulleted_list_item', 'numbered_list_item', 'to_do', 'toggle', 'quote', 'callout']:
            continue
        
        block_content = block.get(block_type, {})
        rich_text = block_content.get('rich_text', [])
        
        if not rich_text:
            continue
        
        # Check each rich_text element for markdown links
        for idx, rt_item in enumerate(rich_text):
            if rt_item.get('type') != 'text':
                continue
            
            text = rt_item.get('text', {}).get('content', '')
            matches = list(re.finditer(markdown_pattern, text))
            
            if not matches:
                continue
            
            for match in matches:
                links_found += 1
                link_text = match.group(1)
                evernote_url = match.group(2)
                
                # Remove 🛑 prefix if present
                clean_link_text = link_text
                if link_text.startswith('🛑 unresolved: '):
                    clean_link_text = link_text[len('🛑 unresolved: '):]
                
                # Try to find match in canonical
                normalized_search = re.sub(r'\s+', ' ', clean_link_text.replace('\xa0', ' ')).strip().lower()
                
                if normalized_search in normalized_map:
                    candidates = normalized_map[normalized_search]
                    if len(candidates) == 1:
                        target_id, target_title = candidates[0]
                        print(f"    ✓ Resolving: '{clean_link_text}' → {target_title}")
                        
                        # Update the block - replace markdown with page mention
                        new_rich_text = rich_text.copy()
                        
                        # Build replacement: text before + mention + text after
                        before_text = text[:match.start()]
                        after_text = text[match.end():]
                        
                        new_elements = []
                        
                        # Add before text if exists
                        if before_text:
                            new_elements.append({
                                'type': 'text',
                                'text': {'content': before_text},
                                'annotations': rt_item.get('annotations', {})
                            })
                        
                        # Add page mention
                        new_elements.append({
                            'type': 'mention',
                            'mention': {
                                'type': 'page',
                                'page': {'id': target_id}
                            }
                        })
                        
                        # Add after text if exists
                        if after_text:
                            new_elements.append({
                                'type': 'text',
                                'text': {'content': after_text},
                                'annotations': rt_item.get('annotations', {})
                            })
                        
                        # Replace the rich_text element
                        new_rich_text = rich_text[:idx] + new_elements + rich_text[idx+1:]
                        
                        # Update the block
                        try:
                            wrapper.update_block(block['id'], {block_type: {'rich_text': new_rich_text}})
                            updates_made += 1
                        except Exception as e:
                            print(f"      ✗ Failed to update: {e}")
                        
                        # Only process one link per rich_text element for now
                        break
    
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Markdown links found:  {links_found}")
    print(f"  Links resolved:        {updates_made}")
    print("=" * 80)
