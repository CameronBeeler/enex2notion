#!/usr/bin/env python3
"""Debug script to inspect why link detection is failing."""
import json
import os
import re
from pathlib import Path

from enex2notion.notion_api_wrapper import NotionAPIWrapper
from enex2notion.link_resolver import find_evernote_links_in_page, MARKDOWN_LINK_PATTERN, EVERNOTE_URL_PATTERN

# Get page ID from completed.json
completed_path = Path.home() / "Downloads" / "imports" / "LinkResolutions" / "completed.json"
with open(completed_path) as f:
    completed = json.load(f)

# Get first page
page_id = completed[0]["id"]
page_title = completed[0]["title"]

print(f"Inspecting page: {page_title}")
print(f"Page ID: {page_id}")
print("=" * 80)

# Initialize wrapper
token = os.environ.get("NOTION_TOKEN")
if not token:
    print("ERROR: NOTION_TOKEN not set")
    exit(1)

wrapper = NotionAPIWrapper(token)

# Get blocks
print("\nFetching blocks...")
blocks = wrapper.get_blocks(page_id)
print(f"Found {len(blocks)} top-level blocks")

# Scan for any text content containing "evernote"
def scan_blocks_recursive(blocks, depth=0):
    """Recursively scan blocks for any evernote-related content."""
    indent = "  " * depth
    
    for i, block in enumerate(blocks):
        block_type = block.get("type")
        block_id = block.get("id", "")[:8]
        
        print(f"\n{indent}Block {i} [{block_type}] {block_id}")
        
        # Check rich_text content
        if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", 
                          "bulleted_list_item", "numbered_list_item", "to_do", 
                          "quote", "callout", "toggle"]:
            rich_text = block.get(block_type, {}).get("rich_text", [])
            print(f"{indent}  rich_text array length: {len(rich_text)}")
            
            for rt_idx, rt_item in enumerate(rich_text):
                rt_type = rt_item.get("type")
                print(f"{indent}    [{rt_idx}] type={rt_type}")
                
                if rt_type == "text":
                    content = rt_item.get("text", {}).get("content", "")
                    href = rt_item.get("text", {}).get("link", {}).get("url", "") if rt_item.get("text", {}).get("link") else ""
                    
                    # Check for evernote content
                    if "evernote" in content.lower() or "evernote" in href.lower():
                        print(f"{indent}      ⚠️  EVERNOTE FOUND!")
                        print(f"{indent}      content: {content[:100]}")
                        if href:
                            print(f"{indent}      href: {href}")
                        
                        # Test regex patterns
                        md_match = MARKDOWN_LINK_PATTERN.search(content)
                        url_match = EVERNOTE_URL_PATTERN.match(href) if href else None
                        print(f"{indent}      markdown pattern match: {bool(md_match)}")
                        print(f"{indent}      url pattern match: {bool(url_match)}")
                    else:
                        # Show first 50 chars
                        preview = content[:50] if len(content) > 50 else content
                        if preview:
                            print(f"{indent}      content: {preview}")
        
        # Check table_row cells
        elif block_type == "table_row":
            cells = block.get("table_row", {}).get("cells", [])
            print(f"{indent}  cells: {len(cells)}")
            for cell_idx, cell in enumerate(cells):
                for rt_idx, rt_item in enumerate(cell):
                    if rt_item.get("type") == "text":
                        content = rt_item.get("text", {}).get("content", "")
                        if "evernote" in content.lower():
                            print(f"{indent}    ⚠️  EVERNOTE in cell [{cell_idx},{rt_idx}]: {content[:50]}")
        
        # Recurse into children
        if block.get("has_children") and "_children" in block:
            print(f"{indent}  Children:")
            scan_blocks_recursive(block["_children"], depth + 1)

scan_blocks_recursive(blocks)

# Now run official link detection
print("\n" + "=" * 80)
print("Running official find_evernote_links_in_page()...")
link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
print(f"Official detection found: {len(link_refs)} links")

if link_refs:
    for ref in link_refs:
        print(f"  - '{ref.link_text}' -> {ref.original_url}")
else:
    print("  (none)")
