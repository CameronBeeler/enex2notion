#!/usr/bin/env python3
"""Check for User Action Required databases."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from enex2notion.notion_api_wrapper import NotionAPIWrapper

token = os.environ.get("NOTION_TOKEN")
if not token:
    print("ERROR: Set NOTION_TOKEN environment variable")
    sys.exit(1)

wrapper = NotionAPIWrapper(token)

# Search for User Action Required databases
print("Searching for 'User Action Required' databases...")
results = wrapper.search_pages("User Action Required", include_databases=True)

# Filter active databases only
active_dbs = [
    r for r in results 
    if r.get("object") == "database" 
    and not r.get("archived", False) 
    and not r.get("in_trash", False)
]

print(f"\nFound {len(active_dbs)} active database(s):\n")

for i, db in enumerate(active_dbs, 1):
    db_id = db["id"]
    parent_id = db.get("parent", {}).get("page_id", "N/A")
    print(f"{i}. ID: {db_id}")
    print(f"   Parent: {parent_id}")
    print()
