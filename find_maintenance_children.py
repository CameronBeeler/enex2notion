#!/usr/bin/env python3
"""
Find pages that are children (database rows) of maintenance databases.
Requires NOTION_TOKEN environment variable.
"""
import os
import json
from pathlib import Path
from notion_client import Client

# Initialize Notion client
token = os.getenv("NOTION_TOKEN")
if not token:
    print("ERROR: NOTION_TOKEN environment variable not set")
    exit(1)

client = Client(auth=token)

# Maintenance database IDs (from clean_canonical.py output)
MAINTENANCE_DB_IDS = {
    "2e636504-73e9-8112-ae94-df06cfdc8fb6",  # DuplicatePageNames
    "2e536504-73e9-81bd-b150-f0690017b748",  # Exceptions
    "2e536504-73e9-81d1-a1b7-fa6a007ed390",  # EvernoteLinkFailure
}

canonical_path = Path.home() / "Downloads" / "imports" / "LinkResolutions" / "canonical.json"

if not canonical_path.exists():
    print(f"Canonical not found at {canonical_path}")
    exit(1)

# Load canonical
with open(canonical_path) as f:
    page_map = json.load(f)

print(f"Total pages in canonical: {len(page_map)}")
print(f"\nChecking which pages are children of maintenance databases...")
print("(This will make API calls for each page - may take a while)\n")

maintenance_children = []
checked = 0

for page_id, title in list(page_map.items()):
    try:
        # Get page details
        page = client.pages.retrieve(page_id=page_id)
        parent = page.get("parent", {})
        
        # Check if parent is a maintenance database
        if parent.get("type") == "database_id":
            parent_db_id = parent.get("database_id")
            if parent_db_id in MAINTENANCE_DB_IDS:
                maintenance_children.append((page_id, title, parent_db_id))
        
        checked += 1
        if checked % 100 == 0:
            print(f"  Checked {checked}/{len(page_map)} pages, found {len(maintenance_children)} maintenance children...")
    
    except Exception as e:
        print(f"  Error checking {page_id}: {e}")
        continue

print(f"\n✓ Found {len(maintenance_children)} pages that are children of maintenance databases:")
for page_id, title, parent_db_id in maintenance_children[:20]:
    print(f"  - {title[:60]} (parent: {parent_db_id[:8]}...)")

if len(maintenance_children) > 20:
    print(f"  ... and {len(maintenance_children) - 20} more")

print(f"\nThese {len(maintenance_children)} pages should be excluded from canonical.")
print(f"After removal: {len(page_map)} - {len(maintenance_children)} = {len(page_map) - len(maintenance_children)} pages")
