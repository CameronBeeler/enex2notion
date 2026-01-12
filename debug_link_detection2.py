#!/usr/bin/env python3
"""Debug script to check multiple pages and understand block retrieval."""
import json
import os
from pathlib import Path

from enex2notion.notion_api_wrapper import NotionAPIWrapper

# Get pages from completed.json
completed_path = Path.home() / "Downloads" / "imports" / "LinkResolutions" / "completed.json"
with open(completed_path) as f:
    completed = json.load(f)

# Initialize wrapper
token = os.environ.get("NOTION_TOKEN")
if not token:
    print("ERROR: NOTION_TOKEN not set")
    exit(1)

wrapper = NotionAPIWrapper(token)

# Check first 10 pages
print("Checking first 10 pages for block content...")
print("=" * 80)

for i, page_entry in enumerate(completed[:10]):
    page_id = page_entry["id"]
    page_title = page_entry["title"]
    
    try:
        # Get page object first
        page_obj = wrapper.client.pages.retrieve(page_id=page_id)
        archived = page_obj.get("archived", False)
        
        # Get blocks
        blocks = wrapper.get_blocks(page_id)
        
        # Count text content
        text_blocks = sum(1 for b in blocks if b.get("type") in [
            "paragraph", "heading_1", "heading_2", "heading_3",
            "bulleted_list_item", "numbered_list_item", "to_do",
            "quote", "callout", "toggle"
        ])
        
        status = "ARCHIVED" if archived else f"{len(blocks)} blocks ({text_blocks} text)"
        print(f"{i+1}. [{status}] {page_title}")
        
        # If has blocks, sample first block
        if blocks:
            first = blocks[0]
            block_type = first.get("type")
            if block_type in ["paragraph", "bulleted_list_item"]:
                rich_text = first.get(block_type, {}).get("rich_text", [])
                if rich_text and rich_text[0].get("type") == "text":
                    content = rich_text[0].get("text", {}).get("content", "")[:60]
                    print(f"   First block preview: {content}")
    except Exception as e:
        print(f"{i+1}. [ERROR: {e}] {page_title}")

print("\n" + "=" * 80)
print("Recommendation:")
print("Pick a page with blocks and manually check if it contains evernote:// links")
