"""Exception tracking for partial imports.

Manages the "Exceptions" summary page structure with real-time updates:
- Root Page → Exceptions (page) → Notebook.enex (pages) → Links to partial import notes
"""
import logging
import time
from pathlib import Path
from typing import Any, Optional

from enex2notion.infrastructure_cache import InfrastructureCache

logger = logging.getLogger(__name__)


class ExceptionTracker:
    """Tracks partial imports and maintains exception summary pages."""

    def __init__(self, wrapper, root_id: str, working_dir: Optional[Path] = None):
        """Initialize exception tracker.

        Args:
            wrapper: NotionAPIWrapper instance
            root_id: Root page ID for creating exception pages
            working_dir: Directory for cache file (defaults to current directory)
        """
        self.wrapper = wrapper
        self.root_id = root_id
        self._exceptions_page_id = None
        self._notebook_exception_pages = {}  # notebook_name -> page_id
        self._special_pages_cache = {}  # title -> page_id (cached after first lookup/create)
        self._exceptions_database_id = None  # Database ID for user-actionable exceptions
        self._exception_counter = {}  # Counter for generating unique titles
        
        # Initialize infrastructure cache
        cache_dir = working_dir or Path.cwd()
        self._cache = InfrastructureCache(cache_dir)
        logger.debug(f"Using cache directory: {cache_dir}")
    
    def initialize_infrastructure(self):
        """Pre-create Exceptions page and User Action Required database.
        
        Call this at the start of import/link resolution to ensure infrastructure
        exists before any processing begins.
        """
        logger.info("Initializing exception tracking infrastructure...")
        
        # Create Exceptions page
        exceptions_page_id = self.ensure_exceptions_page()
        logger.info(f"✓ Exceptions page ready: {exceptions_page_id}")
        
        # Create User Action Required database (and clean up duplicates)
        self._create_exceptions_database()
        if self._exceptions_database_id:
            logger.info(f"✓ User Action Required database ready: {self._exceptions_database_id}")
            
            # Check for and clean up duplicate databases
            self._cleanup_duplicate_databases()
        
        logger.info("Exception tracking infrastructure initialized")

    def ensure_exceptions_page(self) -> str:
        """Get or create the main "Exceptions" page under root.

        Returns:
            Exception page ID
        """
        if self._exceptions_page_id:
            return self._exceptions_page_id
        
        # Check cache first
        cached_id = self._cache.get_exceptions_page_id()
        if cached_id:
            logger.info(f"Using Exceptions page from cache: {cached_id}")
            # Verify the page exists and isn't archived
            try:
                page = self.wrapper.client.pages.retrieve(page_id=cached_id)
                if page.get("archived") or page.get("in_trash"):
                    logger.warning(f"Cached Exceptions page is archived/trashed, will create new one")
                    self._cache.set_exceptions_page_id("")  # Clear invalid cache
                    cached_id = None
                else:
                    self._exceptions_page_id = cached_id
                    logger.info(f"✓ Exceptions page validated")
                    return cached_id
            except Exception as e:
                logger.warning(f"Cached Exceptions page not accessible: {e}")
                self._cache.set_exceptions_page_id("")  # Clear invalid cache
                cached_id = None
        
        if not cached_id:
            logger.debug("No valid Exceptions page in cache")

        # Search for existing "Exceptions" page
        logger.debug("Searching for existing 'Exceptions' page...")
        pages = self.wrapper.search_pages("Exceptions")
        
        # Filter out archived/deleted items
        active_pages = [p for p in pages if not p.get("archived", False) and not p.get("in_trash", False)]

        for page in active_pages:
            if page.get("parent", {}).get("page_id") == self.root_id:
                self._exceptions_page_id = page["id"]
                logger.info("Found existing 'Exceptions' summary page")
                self._cache.set_exceptions_page_id(self._exceptions_page_id)
                return self._exceptions_page_id

        # Create new exceptions page
        logger.info("Creating 'Exceptions' summary page...")
        page = self.wrapper.create_page(parent_id=self.root_id, title="Exceptions")
        self._exceptions_page_id = page["id"]
        self._cache.set_exceptions_page_id(self._exceptions_page_id)

        # Add intro paragraph
        intro_blocks = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "This page lists all partially imported notes with errors. "
                                "Each notebook has a child page listing its exceptions."
                            },
                        }
                    ]
                },
            }
        ]
        self.wrapper.append_blocks(block_id=self._exceptions_page_id, children=intro_blocks)

        return self._exceptions_page_id

    def _create_exceptions_database(self):
        """Create the user-actionable exceptions database under the Exceptions page.
        
        This database tracks items that require manual user intervention:
        - Files that failed to upload (need manual upload from unsupported-files directory)
        - Invalid URLs that need manual fixing
        - Tables that were split and may need manual review/consolidation
        """
        # Check if we already have a cached database ID
        if self._exceptions_database_id:
            logger.debug(f"Found cached database ID: {self._exceptions_database_id}, validating...")
            # Verify it still exists and is valid
            try:
                db_schema = self.wrapper.get_database(self._exceptions_database_id)
                properties = db_schema.get("properties", {})
                if "Error Type" in properties:
                    logger.info(f"Using existing 'User Action Required' database: {self._exceptions_database_id}")
                    return
                else:
                    # Cached DB has wrong schema, clear cache and recreate
                    logger.warning(f"Cached database {self._exceptions_database_id} has wrong schema (missing 'Error Type'), will recreate")
                    self._exceptions_database_id = None
            except Exception as e:
                error_msg = str(e).lower()
                # Check if it's a permission/access error - these should keep the cache
                # The database exists, we just can't validate it right now
                if "could not find" in error_msg or "access" in error_msg or "permission" in error_msg:
                    logger.info(f"Using existing 'User Action Required' database: {self._exceptions_database_id} (unable to validate, assuming valid)")
                    return
                else:
                    # Database truly doesn't exist anymore, clear cache
                    logger.warning(f"Cached database {self._exceptions_database_id} no longer exists: {e}")
                    logger.warning("Will search for existing database or create new one")
                    self._exceptions_database_id = None
        
        # Ensure Exceptions page exists first
        exceptions_page_id = self.ensure_exceptions_page()
        
        # Check cache first
        cached_db_id = self._cache.get_database_id("User Action Required")
        if cached_db_id:
            logger.info(f"Found 'User Action Required' database in cache file: {cached_db_id}")
            # Verify it still exists and is accessible
            try:
                db_schema = self.wrapper.get_database(cached_db_id)
                # Database exists and is accessible - trust the cache
                logger.info(f"✓ Using cached database: {cached_db_id}")
                self._exceptions_database_id = cached_db_id
                self._cleanup_duplicate_databases()
                return
            except Exception as e:
                error_msg = str(e).lower()
                if "could not find" in error_msg or "object not found" in error_msg:
                    logger.warning(f"Cached database not found: {e}")
                    self._cache.clear_database("User Action Required")
                else:
                    # Other errors (like permissions) - keep using the cache
                    logger.info(f"Using cached database (unable to verify): {cached_db_id}")
                    self._exceptions_database_id = cached_db_id
                    return
        
        # Search Notion for existing database - search ALL databases, then filter by parent
        logger.info("Searching Notion for existing 'User Action Required' database...")
        databases = self.wrapper.search_pages("User Action Required", include_databases=True)
        
        # Filter out archived/deleted items (in_trash or archived=true)
        active_databases = [
            d for d in databases 
            if d.get("object") == "database" 
            and not d.get("archived", False)
            and not d.get("in_trash", False)
        ]
        
        # Look for any existing database with this name under the exceptions page
        found_db_id = None
        if len(active_databases) > 0:
            logger.info(f"Found {len(active_databases)} active database(s) with name 'User Action Required', validating...")
        else:
            logger.info("No existing 'User Action Required' database found")
            databases_in_trash = len(databases) - len(active_databases)
            if databases_in_trash > 0:
                logger.debug(f"  ({databases_in_trash} database(s) found in trash, ignoring)")
        
        for db in active_databases:
                db_id = db["id"]
                parent = db.get("parent", {})
                parent_page_id = parent.get("page_id")
                
                logger.debug(f"  Checking database {db_id}, parent: {parent_page_id}")
                
                # Check if parent is the exceptions page
                if parent.get("type") == "page_id" and parent_page_id == exceptions_page_id:
                    logger.info(f"  ✓ Database {db_id} is under Exceptions page, validating schema...")
                    found_db_id = db_id
                    # Verify it has the correct schema by checking for "Error Type" property
                    try:
                        db_schema = self.wrapper.get_database(found_db_id)
                        properties = db_schema.get("properties", {})
                        if "Error Type" in properties:
                            self._exceptions_database_id = found_db_id
                            logger.info(f"Found existing 'User Action Required' database: {self._exceptions_database_id}")
                            # Cache the database ID
                            self._cache.set_database_id("User Action Required", self._exceptions_database_id)
                            # Clean up duplicates immediately
                            self._cleanup_duplicate_databases()
                            return
                        else:
                            # Database exists but has wrong schema - delete and recreate
                            logger.warning(f"  ✗ Database has wrong schema (missing 'Error Type'), deleting: {found_db_id}")
                            self.wrapper.delete_block(found_db_id)
                            break
                    except Exception as e:
                        logger.warning(f"  ✗ Failed to validate database schema: {e}")
                        break
                else:
                    logger.debug(f"  Skipping database {db_id} (wrong parent)")
        
        # If we found databases with this name but under different parents, delete them
        # to avoid confusion (this handles the multiple databases issue)
        for db in databases:
            if db.get("object") == "database":
                parent = db.get("parent", {})
                if parent.get("type") == "page_id" and parent.get("page_id") != exceptions_page_id:
                    db_id = db["id"]
                    logger.warning(f"Found 'User Action Required' database in wrong location ({db_id}), deleting...")
                    try:
                        self.wrapper.delete_block(db_id)
                        logger.info(f"Deleted misplaced database: {db_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete misplaced database: {e}")
        
        # Create new database with schema
        logger.info("Creating 'User Action Required' database...")
        schema = {
            "Title": {"title": {}},  # Descriptive title: <note-title>-<error-type>-<count>
            "Import Source": {"rich_text": {}},  # Source ENEX filename
            "Notion Source Page": {"rich_text": {}},  # Page title in Notion
            "Resolved": {"checkbox": {}},  # User marks as resolved after fixing
            "Error Type": {"select": {}},  # Use select instead of status (simpler, no options needed)
            "Block Link": {"url": {}},  # Direct link to the block/page in Notion
        }
        
        db = self.wrapper.create_database(
            parent_id=exceptions_page_id,
            title="User Action Required",
            properties_schema=schema
        )
        self._exceptions_database_id = db["id"]
        logger.info(f"Created 'User Action Required' database: {self._exceptions_database_id}")
        
        # Cache the database ID
        self._cache.set_database_id("User Action Required", self._exceptions_database_id)
        
        # Wait for Notion to propagate the new database so search will find it
        logger.debug("Waiting 5 seconds for database to propagate in Notion's search index...")
        time.sleep(5)
        
        # Clean up any duplicates immediately after creation
        self._cleanup_duplicate_databases()
    
    def _cleanup_duplicate_databases(self):
        """Find and delete duplicate 'User Action Required' databases.
        
        Keeps only the database stored in self._exceptions_database_id and deletes
        all others with the same name.
        """
        if not self._exceptions_database_id:
            return
        
        logger.debug("Checking for duplicate 'User Action Required' databases...")
        
        try:
            # Search for all databases with this name
            databases = self.wrapper.search_pages("User Action Required", include_databases=True)
            
            duplicates_found = 0
            for db in databases:
                if db.get("object") == "database":
                    db_id = db["id"]
                    # Skip the one we're keeping
                    if db_id == self._exceptions_database_id:
                        continue
                    
                    # Delete any other database with this name
                    try:
                        self.wrapper.delete_block(db_id)
                        duplicates_found += 1
                        logger.info(f"Deleted duplicate 'User Action Required' database: {db_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete duplicate database {db_id}: {e}")
            
            if duplicates_found > 0:
                logger.info(f"Cleaned up {duplicates_found} duplicate database(s)")
            else:
                logger.debug("No duplicate databases found")
        except Exception as e:
            logger.warning(f"Failed to check for duplicate databases: {e}")
    
    def add_exception_to_database(
        self,
        notebook_name: str,
        note_title: str,
        page_id: str,
        error_type: str,
        error_detail: str = "",
        block_id: str = None
    ):
        """Add a user-actionable exception entry to the database.
        
        Args:
            notebook_name: Name of the source ENEX file (e.g., "Decisions.enex")
            note_title: Title of the note in Notion
            page_id: Page ID of the note in Notion
            error_type: Type of error - must be one of: "File Upload Failed", "Invalid URL", "Table Split"
            error_detail: Additional details (e.g., filename, URL, etc.)
            block_id: Optional block ID to link directly to the user action marker block
        """
        # Verify the page exists and isn't trashed
        try:
            page = self.wrapper.client.pages.retrieve(page_id=page_id)
            if page.get("archived") or page.get("in_trash"):
                logger.debug(f"Skipping exception database entry for trashed/archived page: {note_title}")
                return
        except Exception as e:
            logger.debug(f"Skipping exception database entry for inaccessible page {note_title}: {e}")
            return
        
        # Ensure database exists
        self.ensure_exceptions_page()
        if not self._exceptions_database_id:
            self._create_exceptions_database()
        
        # Generate unique title
        counter_key = f"{notebook_name}-{note_title}-{error_type}"
        count = self._exception_counter.get(counter_key, 0) + 1
        self._exception_counter[counter_key] = count
        
        # Format title: <note-title>-<error-type>-<count>
        # Truncate note title to keep total length reasonable
        safe_note_title = note_title[:80] if note_title else "Untitled"
        title_text = f"{safe_note_title}-{error_type.replace(' ', '')}-{count}"
        
        # Create block link - use block-level link if block_id provided, otherwise page-level
        if block_id:
            # Create direct block link: https://notion.so/<page_id_no_dashes>#<block_id_no_dashes>
            clean_page_id = page_id.replace('-', '')
            clean_block_id = block_id.replace('-', '')
            block_link = f"https://notion.so/{clean_page_id}#{clean_block_id}"
        else:
            # Fall back to page-level link
            block_link = f"https://notion.so/{page_id.replace('-', '')}"
        
        # Create database entry
        properties = {
            "Title": {"title": [{"type": "text", "text": {"content": title_text}}]},
            "Import Source": {"rich_text": [{"type": "text", "text": {"content": notebook_name}}]},
            "Notion Source Page": {"rich_text": [{"type": "text", "text": {"content": note_title}}]},
            "Resolved": {"checkbox": False},
            "Error Type": {"select": {"name": error_type}},
            "Block Link": {"url": block_link},
        }
        
        try:
            self.wrapper.create_page(
                parent_id=self._exceptions_database_id,
                title=title_text,
                properties=properties
            )
            logger.debug(f"Added exception to database: {title_text} ({error_detail})")
        except Exception as e:
            logger.warning(f"Failed to add exception to database: {e}")

    def ensure_notebook_exception_page(self, notebook_name: str) -> str:
        """Get or create exception page for a specific notebook.

        Args:
            notebook_name: Notebook name (e.g., "MyNotebook.enex")

        Returns:
            Notebook exception page ID
        """
        if notebook_name in self._notebook_exception_pages:
            return self._notebook_exception_pages[notebook_name]

        exceptions_page_id = self.ensure_exceptions_page()

        # Search for existing notebook exception page as child of Exceptions page
        page_title = f"{notebook_name}"
        logger.debug(f"Searching for existing exception page for '{page_title}'...")
        pages = self.wrapper.search_pages(page_title)
        
        # Filter out archived/deleted items
        active_pages = [p for p in pages if not p.get("archived", False) and not p.get("in_trash", False)]

        for page in active_pages:
            if page.get("parent", {}).get("page_id") == exceptions_page_id:
                page_id = page["id"]
                self._notebook_exception_pages[notebook_name] = page_id
                logger.debug(f"Found existing notebook exception page: {page_title}")
                return page_id

        # Create new notebook exception page
        logger.debug(f"Creating notebook exception page: {page_title}")
        page = self.wrapper.create_page(parent_id=exceptions_page_id, title=page_title)
        page_id = page["id"]
        self._notebook_exception_pages[notebook_name] = page_id

        # Add intro paragraph
        intro_blocks = [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Partial Import Exceptions"}}]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "Notes below encountered errors during import. "
                                "Click each link to see the note with inline error details."
                            },
                        }
                    ]
                },
            },
            {"object": "block", "type": "divider", "divider": {}},
        ]
        self.wrapper.append_blocks(block_id=page_id, children=intro_blocks)

        return page_id

    def track_partial_import(
        self, notebook_name: str, note_title: str, page_id: str, errors: list[str]
    ):
        """Record a partial import exception and append to notebook exception page.

        Args:
            notebook_name: Name of notebook
            note_title: Title of note that had partial import
            page_id: Notion page ID of the partially imported note
            errors: List of error messages
        """
        # Verify the page exists and isn't trashed
        try:
            page = self.wrapper.client.pages.retrieve(page_id=page_id)
            if page.get("archived") or page.get("in_trash"):
                logger.debug(f"Skipping exception tracking for trashed/archived page: {note_title}")
                return
        except Exception as e:
            logger.debug(f"Skipping exception tracking for inaccessible page {note_title}: {e}")
            return
        
        notebook_exception_page_id = self.ensure_notebook_exception_page(notebook_name)

        # Create blocks to append
        blocks = []

        # Bullet point with link to note
        blocks.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "mention",
                            "mention": {"type": "page", "page": {"id": page_id}},
                            "annotations": {"bold": True},
                        },
                        {"type": "text", "text": {"content": f" - {note_title}"}},
                    ]
                },
            }
        )

        # Indented error messages as nested bullets
        if errors:
            error_items = []
            for error in errors[:10]:  # Limit to first 10 errors to avoid huge lists
                error_items.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": error}}],
                            "color": "red",
                        },
                    }
                )

            if len(errors) > 10:
                error_items.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": f"... and {len(errors) - 10} more errors"},
                                }
                            ],
                            "color": "gray",
                        },
                    }
                )

            # Append error items as children to the main bullet
            # Note: Official API requires separate append for nested children
            # We'll append the main bullet first, then its children
            try:
                result = self.wrapper.append_blocks(
                    block_id=notebook_exception_page_id, children=[blocks[0]]
                )
                if result and len(result) > 0:
                    parent_block_id = result[0]["id"]
                    self.wrapper.append_blocks(block_id=parent_block_id, children=error_items)
            except Exception as e:
                logger.error(f"Failed to append exception entry: {e}")
                logger.debug(e, exc_info=e)
        else:
            # No errors to nest, just append the main bullet
            try:
                self.wrapper.append_blocks(block_id=notebook_exception_page_id, children=blocks)
            except Exception as e:
                logger.error(f"Failed to append exception entry: {e}")
                logger.debug(e, exc_info=e)

        logger.debug(f"Tracked partial import for note '{note_title}' in notebook '{notebook_name}'")

    # New: generic special exception page and unmatched link tracking
    def _ensure_special_child_page(self, title: str, recreate: bool = False) -> str:
        """Get or create a special exception child page.
        
        Args:
            title: Page title
            recreate: If True, delete ALL existing pages with this title and create new one
        
        Returns:
            Page ID
        """
        # Return cached ID if we already have it and not recreating
        if not recreate and title in self._special_pages_cache:
            return self._special_pages_cache[title]
        
        exceptions_page_id = self.ensure_exceptions_page()
        
        # Delete ALL existing pages with this title if recreate requested
        if recreate:
            pages = self.wrapper.search_pages(title)
            deleted_count = 0
            deleted_ids = []
            for page in pages:
                if page.get("parent", {}).get("page_id") == exceptions_page_id:
                    try:
                        page_id = page["id"]
                        self.wrapper.delete_block(block_id=page_id)
                        deleted_ids.append(page_id)
                        deleted_count += 1
                        logger.info(f"Deleted existing exception page: {title} ({page_id})")
                    except Exception as e:
                        logger.warning(f"Failed to delete page '{title}' ({page['id']}): {e}")
            
            if deleted_count > 0:
                if deleted_count > 1:
                    logger.info(f"Deleted {deleted_count} duplicate '{title}' pages")
                # Wait for deletion to propagate in Notion's system
                logger.debug(f"Waiting 2s for deletion to propagate...")
                time.sleep(2)
            
            # Clear cache since we deleted
            self._special_pages_cache.pop(title, None)
        
        # Find existing page (if not recreating) - use FIRST match
        if not recreate:
            pages = self.wrapper.search_pages(title)
            for page in pages:
                if page.get("parent", {}).get("page_id") == exceptions_page_id:
                    page_id = page["id"]
                    self._special_pages_cache[title] = page_id
                    logger.debug(f"Found existing exception page: {title}")
                    return page_id
        
        # Create new page
        logger.info(f"Creating new exception page: {title}")
        page = self.wrapper.create_page(parent_id=exceptions_page_id, title=title)
        page_id = page["id"]
        self._special_pages_cache[title] = page_id
        return page_id

    def track_unmatched_link(self, source_page_title: str, source_page_id: str, link_text: str, original_url: str, block_id: str = None, recreate: bool = False):
        """Record an unmatched evernote link.

        Appends a toggle to Exceptions → EvernoteLinkFailure with a mention to the source page,
        the link_text used for matching, the original URL, and optional block URL.
        """
        page_id = self._ensure_special_child_page("EvernoteLinkFailure", recreate=recreate)
        
        # Build rich_text with page mention and link details
        rich_text = [
            {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
            {"type": "text", "text": {"content": f" – '{link_text}' → {original_url}"}},
        ]
        
        # Add block URL if provided
        if block_id:
            clean_block_id = block_id.replace("-", "")
            sp = source_page_id.replace("-", "") if source_page_id else ""
            block_url = f"https://www.notion.so/{sp}#{clean_block_id}" if sp else f"https://www.notion.so/{clean_block_id}"
            rich_text.extend([
                {"type": "text", "text": {"content": " [Block: "}},
                {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                {"type": "text", "text": {"content": "]"}},
            ])
        
        toggle = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": rich_text
            }
        }
        try:
            self.wrapper.append_blocks(block_id=page_id, children=[toggle])
        except Exception as e:
            logger.warning(f"Failed to append unmatched link entry: {e}")

    def track_ambiguous_link(self, source_page_title: str, source_page_id: str, link_text: str, candidate_ids: list[tuple[str, str]], block_id: str = None, recreate: bool = False):
        """Record an ambiguous evernote link with multiple candidate pages.

        Appends a toggle to Exceptions → UnresolvableEvernoteLinks with a mention to the source page,
        the link_text used for matching, and a sub-list of candidate page mentions.
        """
        page_id = self._ensure_special_child_page("UnresolvableEvernoteLinks", recreate=recreate)
        
        # Build rich_text for toggle header
        rich_text = [
            {"type": "mention", "mention": {"type": "page", "page": {"id": source_page_id}}},
            {"type": "text", "text": {"content": f" – ambiguous '{link_text}' (multiple pages found)"}},
        ]
        
        # Add block URL if provided
        if block_id:
            clean_block_id = block_id.replace("-", "")
            sp = source_page_id.replace("-", "") if source_page_id else ""
            block_url = f"https://www.notion.so/{sp}#{clean_block_id}" if sp else f"https://www.notion.so/{clean_block_id}"
            rich_text.extend([
                {"type": "text", "text": {"content": " [Block: "}},
                {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                {"type": "text", "text": {"content": "]"}},
            ])
        
        # Parent toggle
        parent = {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": rich_text
            }
        }
        # Children bullets for candidates
        children = []
        for cid, ctitle in candidate_ids[:10]:
            children.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "mention", "mention": {"type": "page", "page": {"id": cid}}},
                        {"type": "text", "text": {"content": f" – {ctitle}"}},
                    ]
                }
            })
        try:
            result = self.wrapper.append_blocks(block_id=page_id, children=[parent])
            if result and children:
                parent_id = result[0]["id"]
                self.wrapper.append_blocks(block_id=parent_id, children=children)
        except Exception as e:
            logger.warning(f"Failed to append ambiguous link entry: {e}")

    def track_duplicate_page_names(self, duplicates: dict[str | None, list[str]], recreate: bool = True):
        """Record duplicate page names and link to all duplicates.

        Args:
            duplicates: mapping title -> list of page_ids with that title
                      (title can be None for blank titles)
        """
        page_id = self._ensure_special_child_page("DuplicatePageNames", recreate=recreate)
        
        for title, ids in duplicates.items():
            if len(ids) < 2 and title is not None:
                continue
            
            # Use "Blank-Page-Titles" for None/empty titles
            display_title = "Blank-Page-Titles" if title is None or title == "" else title
            
            # Parent toggle with the title
            parent = {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": display_title}}]
                }
            }
            
            # Append parent, then children with page mentions and block URLs
            try:
                res = self.wrapper.append_blocks(block_id=page_id, children=[parent])
                if res:
                    parent_block_id = res[0]["id"]
                    child_bullets = []
                    for pid in ids:
                        # Create block URL
                        clean_id = pid.replace("-", "")
                        block_url = f"https://www.notion.so/{clean_id}"
                        
                        child_bullets.append({
                            "object": "block",
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {
                                "rich_text": [
                                    {"type": "mention", "mention": {"type": "page", "page": {"id": pid}}},
                                    {"type": "text", "text": {"content": " – "}},
                                    {"type": "text", "text": {"content": block_url, "link": {"url": block_url}}},
                                ]
                            }
                        })
                    if child_bullets:
                        self.wrapper.append_blocks(block_id=parent_block_id, children=child_bullets)
            except Exception as e:
                logger.warning(f"Failed to log duplicate title '{display_title}': {e}")
