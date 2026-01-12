#!/usr/bin/env python3
"""
Remove maintenance/exception pages AND their children from canonical.json
"""
import json
import os
from pathlib import Path
from notion_client import Client

# Maintenance pages to exclude
MAINTENANCE_PAGE_TITLES = {
    "Exceptions",
    "DuplicatePageNames", 
    "EvernoteLinkFailure",
    "UnresolvableEvernoteLinks",
    "Page-Title mention conversion failures",
}

canonical_path = Path.home() / "Downloads" / "imports" / "LinkResolutions" / "canonical.json"

if not canonical_path.exists():
    print(f"Canonical not found at {canonical_path}")
    exit(1)

# Get Notion token
token = os.getenv("NOTION_TOKEN")
if not token:
    print("ERROR: NOTION_TOKEN environment variable not set")
    print("Will only remove maintenance database pages, not their children")
    client = None
else:
    client = Client(auth=token)

# Load canonical
with open(canonical_path) as f:
    page_map = json.load(f)

original_count = len(page_map)
print(f"Original pages: {original_count}")

# Find maintenance database IDs
maintenance_db_ids = set()
removed_dbs = []
for page_id, title in list(page_map.items()):
    if title in MAINTENANCE_PAGE_TITLES:
        maintenance_db_ids.add(page_id)
        removed_dbs.append((page_id, title))

print(f"\nFound {len(removed_dbs)} maintenance databases:")
for page_id, title in removed_dbs:
    print(f"  - {title} ({page_id})")

if client and maintenance_db_ids:
    print(f"\nChecking for child pages of maintenance databases...")
    removed_children = []
    checked = 0
    
    for page_id, title in list(page_map.items()):
        if page_id in maintenance_db_ids:
            continue  # Skip the databases themselves
        
        try:
            page = client.pages.retrieve(page_id=page_id)
            parent = page.get("parent", {})
            
            if parent.get("type") == "database_id":
                parent_db_id = parent.get("database_id")
                if parent_db_id in maintenance_db_ids:
                    removed_children.append((page_id, title, parent_db_id))
            
            checked += 1
            if checked % 100 == 0:
                print(f"  Checked {checked}/{len(page_map)} pages, found {len(removed_children)} children...")
        
        except Exception as e:
            # Ignore errors, just skip this page
            pass
    
    print(f"\n✓ Found {len(removed_children)} child pages of maintenance databases")
    
    # Remove all maintenance pages and their children
    for page_id, title in removed_dbs:
        del page_map[page_id]
    for page_id, title, parent_id in removed_children:
        del page_map[page_id]
    
    print(f"\nRemoved:")
    print(f"  - {len(removed_dbs)} maintenance databases")
    print(f"  - {len(removed_children)} child pages")
    print(f"  - {len(removed_dbs) + len(removed_children)} total")
else:
    # Just remove the databases themselves
    for page_id, title in removed_dbs:
        del page_map[page_id]
    print(f"\nRemoved {len(removed_dbs)} maintenance databases (no token to check children)")

print(f"\nFinal pages: {len(page_map)}")

# Save cleaned canonical
with open(canonical_path, 'w') as f:
    json.dump(page_map, f, indent=2, ensure_ascii=False)

print(f"\n✓ Saved cleaned canonical to {canonical_path}")
