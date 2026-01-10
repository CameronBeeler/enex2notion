"""Wrapper for official Notion API.

Provides abstraction layer for page/database/block operations
using the official notion-client package with Integration tokens.

Note: Database creation uses raw requests due to notion-client 2.7.0 bug
that silently drops the 'properties' parameter.
"""
import logging
import time
from typing import Any, Callable
import requests
from requests.exceptions import Timeout, ConnectionError

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)

NOTION_API_VERSION = "2022-06-28"


def _retry_on_transient_errors(func: Callable, max_retries: int = 3, initial_delay: float = 1.0) -> Any:
    """Retry a function call on transient errors with exponential backoff.
    
    Args:
        func: Function to call
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds (doubles each retry)
    
    Returns:
        Function result
    
    Raises:
        Last exception if all retries exhausted
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except (Timeout, ConnectionError, APIResponseError) as e:
            last_exception = e
            
            # Check if error is transient (retryable)
            is_transient = False
            error_str = str(e).lower()
            
            if isinstance(e, (Timeout, ConnectionError)):
                is_transient = True
            elif isinstance(e, APIResponseError):
                # Retry on 502 (Bad Gateway), 503 (Service Unavailable), 429 (Rate Limit)
                if hasattr(e, 'code'):
                    is_transient = e.code in [502, 503, 429, 500]
                else:
                    is_transient = any(x in error_str for x in ['502', '503', '429', '500', 'bad gateway', 'timeout'])
            
            if is_transient and attempt < max_retries:
                logger.warning(f"Transient error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                logger.info(f"Retrying in {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                # Non-transient error or max retries reached
                if attempt >= max_retries:
                    logger.error(f"Max retries ({max_retries}) exhausted. Last error: {e}")
                raise
    
    # Should not reach here, but raise last exception if we somehow do
    raise last_exception


class NotionAPIWrapper:
    """Wrapper around official Notion API client."""

    def __init__(self, auth_token: str):
        """Initialize with Integration token.

        Args:
            auth_token: Notion Integration token (starts with secret_)
        """
        # Increase timeout for large file uploads and slow blocks (default is 60s)
        self.client = Client(auth=auth_token, timeout_ms=600000)  # 10 minutes
        self._auth_token = auth_token  # Store for raw API calls
        self._rate_limit_delay = 0.35  # ~3 requests/second
        self._max_retries = 6  # Maximum retry attempts for rate limit errors
        self._max_wait_time = 1500  # 25 minutes in seconds

    def _retry_on_rate_limit(self, func, *args, **kwargs):
        """Retry a function with exponential backoff ONLY on rate limit errors (429).
        
        Uses progressive backoff: 1s, 2s, 4s, then 5min intervals.
        Gives up after 25 minutes total wait time.
        
        Other errors (timeouts, connection issues, 500s, etc.) are raised immediately
        and should be handled by the caller if retry is desired.
        
        Args:
            func: Function to call
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Function result
            
        Raises:
            APIResponseError: If all retries exhausted or non-rate-limit error
        """
        total_wait_time = 0
        
        for attempt in range(self._max_retries):
            try:
                result = func(*args, **kwargs)
                # Log success if we had previous rate limit failures
                if attempt > 0:
                    logger.info("✓ Retry successful after rate limit")
                return result
            except APIResponseError as e:
                error_msg = str(e).lower()
                # ONLY retry on rate limit errors (429)
                is_rate_limit = "rate limit" in error_msg or "429" in error_msg
                
                if not is_rate_limit:
                    # Not a rate limit error - raise immediately for caller to handle
                    raise
                
                # Calculate wait time: 1s, 2s, 4s, then 5min, 5min, 5min
                if attempt < 3:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                else:
                    wait_time = 300  # 5 minutes
                
                # Check if we would exceed max wait time
                if total_wait_time + wait_time > self._max_wait_time:
                    logger.error(
                        f"Rate limiting has persisted for {total_wait_time / 60:.1f} minutes. "
                        f"Exceeded maximum wait time of {self._max_wait_time / 60:.0f} minutes. "
                        "Please restart the import later when rate limits have reset."
                    )
                    raise
                
                # Last attempt check
                if attempt == self._max_retries - 1:
                    logger.error(
                        f"Rate limiting persisted after {self._max_retries} retry attempts. "
                        "Please restart the import later when rate limits have reset."
                    )
                    raise
                
                # Log warning and wait
                wait_min = wait_time / 60
                logger.warning(
                    f"Rate limited by Notion API (attempt {attempt + 1}/{self._max_retries}). "
                    f"Waiting {wait_min:.1f} minute{'s' if wait_min != 1 else ''} before retry..."
                )
                time.sleep(wait_time)
                total_wait_time += wait_time
        
        # Should never reach here, but just in case
        raise APIResponseError(429, "Max retries exhausted", {})

    def search_pages(self, title: str, include_databases: bool = False) -> list[dict[str, Any]]:
        """Search for pages and optionally databases by title.

        Args:
            title: Page/database title to search for
            include_databases: If True, also search for databases

        Returns:
            List of matching page/database objects
        """
        try:
            # Search without filter to get both pages and databases
            logger.debug(f"Searching for '{title}' (include_databases={include_databases})")
            response = self._retry_on_rate_limit(self.client.search, query=title)
            results = response.get("results", [])
            logger.debug(f"  Raw search returned {len(results)} results")
            
            # Debug: show all results
            for i, r in enumerate(results):
                obj_type = r.get("object")
                obj_id = r.get("id")
                if obj_type == "database":
                    obj_title = r.get("title", [{}])[0].get("plain_text", "<no title>")
                else:
                    obj_title = r.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "<no title>")
                logger.debug(f"    [{i}] type={obj_type}, id={obj_id}, title={obj_title}")
            
            # Filter by object type if specified
            if not include_databases:
                results = [r for r in results if r.get("object") == "page"]
                logger.debug(f"  After filtering to pages only: {len(results)} results")
            
            # Match title
            matched = []
            for r in results:
                # For databases, check title array
                if r.get("object") == "database":
                    db_title = r.get("title", [])
                    if db_title and len(db_title) > 0:
                        plain_text = db_title[0].get("plain_text")
                        logger.debug(f"  Checking database: '{plain_text}' == '{title}' ? {plain_text == title}")
                        if plain_text == title:
                            matched.append(r)
                # For pages, check properties.title
                elif r.get("object") == "page":
                    page_title = r.get("properties", {}).get("title", {}).get("title", [{}])
                    if page_title and len(page_title) > 0:
                        plain_text = page_title[0].get("plain_text")
                        logger.debug(f"  Checking page: '{plain_text}' == '{title}' ? {plain_text == title}")
                        if plain_text == title:
                            matched.append(r)
            
            logger.debug(f"  Final matched results: {len(matched)}")
            return matched
        except APIResponseError as e:
            logger.error(f"Search failed: {e}")
            return []

    def create_page(self, parent_id: str | None, title: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a new page.

        Args:
            parent_id: Parent page/database ID
            title: Page title
            properties: Additional properties (for database pages - will replace default)

        Returns:
            Created page object
        """
        if properties:
            # Creating a page in a database; if parent_id is None, create in workspace
            if parent_id:
                parent = {"type": "database_id", "database_id": parent_id}
            else:
                parent = {"type": "workspace", "workspace": True}
            page_data = {
                "parent": parent,
                "properties": properties,
            }
        else:
            # Creating a regular page; if parent_id is None, create as top-level in workspace
            if parent_id:
                parent = {"type": "page_id", "page_id": parent_id}
            else:
                parent = {"type": "workspace", "workspace": True}
            page_data = {
                "parent": parent,
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
            }

        time.sleep(self._rate_limit_delay)
        return self._retry_on_rate_limit(self.client.pages.create, **page_data)

    def create_database(
        self, parent_id: str | None, title: str, properties_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new database.

        Args:
            parent_id: Parent page ID
            title: Database title
            properties_schema: Database properties schema

        Returns:
            Created database object
            
        Note: Uses raw requests instead of notion-client due to library bug
              that drops the 'properties' parameter in version 2.7.0
        """
        if parent_id:
            parent = {"type": "page_id", "page_id": parent_id}
        else:
            parent = {"type": "workspace", "workspace": True}
        database_data = {
            "parent": parent,
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties_schema,
        }
        
        logger.debug(f"Creating database with {len(properties_schema)} properties")

        time.sleep(self._rate_limit_delay)
        
        # Use raw requests API instead of notion-client due to library bug
        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        def _create_db_request():
            response = requests.post(
                "https://api.notion.com/v1/databases",
                headers=headers,
                json=database_data,
                timeout=30,
            )
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", response.text)
                # Raise as a generic Exception since APIResponseError expects a response object
                raise Exception(f"Database creation failed (HTTP {response.status_code}): {error_msg}")
            return response
        
        response = self._retry_on_rate_limit(_create_db_request)
        
        result = response.json()
        logger.debug(f"Database created successfully with ID: {result.get('id')}")
        return result

    def append_blocks(
        self, block_id: str, children: list[dict[str, Any]], max_batch: int = 100
    ) -> list[dict[str, Any]]:
        """Append blocks to a page/block.

        Official API limit: 100 blocks per request.
        This method handles batching automatically.

        Args:
            block_id: Parent block/page ID
            children: List of block objects
            max_batch: Maximum blocks per request (default: 100)

        Returns:
            List of created block objects
        """
        created_blocks = []

        for i in range(0, len(children), max_batch):
            batch = children[i : i + max_batch]
            time.sleep(self._rate_limit_delay)

            try:
                response = self._retry_on_rate_limit(
                    self.client.blocks.children.append, block_id=block_id, children=batch
                )
                created_blocks.extend(response.get("results", []))
                logger.debug(f"Appended {len(batch)} blocks to {block_id}")
            except APIResponseError as e:
                # Rate limit errors are already handled in _retry_on_rate_limit
                # Log as warning for non-rate-limit errors
                error_msg = str(e).lower()
                if "rate limit" in error_msg or "429" in error_msg:
                    logger.warning(f"Failed to append blocks after rate limit retries: {e}")
                else:
                    logger.error(f"Failed to append blocks: {e}")
                raise

        return created_blocks

    def get_block(self, block_id: str) -> dict[str, Any]:
        """Retrieve a block.

        Args:
            block_id: Block ID

        Returns:
            Block object
        """
        time.sleep(self._rate_limit_delay)
        return self._retry_on_rate_limit(self.client.blocks.retrieve, block_id=block_id)

    def get_database(self, database_id: str) -> dict[str, Any]:
        """Retrieve a database and its schema.

        Args:
            database_id: Database ID

        Returns:
            Database object with properties schema
        """
        time.sleep(self._rate_limit_delay)
        return self._retry_on_rate_limit(self.client.databases.retrieve, database_id=database_id)

    def get_blocks(self, block_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """Retrieve all blocks from a page/block with pagination.

        Args:
            block_id: Parent block/page ID
            page_size: Number of blocks per page (max 100)

        Returns:
            List of all block objects (recursively includes nested children)
        """
        all_blocks = []
        start_cursor = None

        while True:
            time.sleep(self._rate_limit_delay)

            params = {"block_id": block_id, "page_size": min(page_size, 100)}
            if start_cursor:
                params["start_cursor"] = start_cursor

            try:
                # Wrap API call with retry logic
                response = self._retry_on_rate_limit(self.client.blocks.children.list, **params)
                blocks = response.get("results", [])
                all_blocks.extend(blocks)

                # Check for nested children
                for block in blocks:
                    if block.get("has_children"):
                        # Recursively get children, but handle unsupported block types
                        try:
                            nested = self.get_blocks(block["id"], page_size)
                            # Store nested children in the block
                            block["_children"] = nested
                        except APIResponseError as e:
                            # Check if it's an unsupported block type error
                            error_msg = str(e).lower()
                            if "not supported via the api" in error_msg:
                                block_type = block.get("type", "unknown")
                                logger.warning(f"Skipping unsupported block type '{block_type}' (block {block['id']}): {e}")
                                # Mark block as having unsupported children so we can skip it later
                                block["_unsupported"] = True
                                block["_children"] = []
                            else:
                                # Re-raise if it's a different error
                                raise

                # Check if there are more pages
                if not response.get("has_more"):
                    break

                start_cursor = response.get("next_cursor")
            except APIResponseError as e:
                logger.error(f"Failed to retrieve blocks from {block_id}: {e}")
                raise

        return all_blocks

    def update_block(self, block_id: str, block_data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing block.

        Args:
            block_id: Block ID to update
            block_data: Block data to update (e.g., {'paragraph': {'rich_text': [...]}})

        Returns:
            Updated block object
        """
        time.sleep(self._rate_limit_delay)

        try:
            return self._retry_on_rate_limit(self.client.blocks.update, block_id=block_id, **block_data)
        except APIResponseError as e:
            logger.error(f"Failed to update block {block_id}: {e}")
            raise

    def delete_block(self, block_id: str) -> dict[str, Any]:
        """Archive (delete) a block or page by ID."""
        time.sleep(self._rate_limit_delay)
        try:
            return self._retry_on_rate_limit(self.client.blocks.delete, block_id=block_id)
        except APIResponseError as e:
            logger.error(f"Failed to delete block {block_id}: {e}")
            raise

    def list_all_pages_recursive(
        self, root_id: str, title_map: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Recursively collect all pages and database records under a root page.

        Args:
            root_id: Root page ID to start traversal
            title_map: Existing title map to add to (for recursion)

        Returns:
            Dictionary mapping page title to page ID: {"Page Title": "page_id"}
        """
        if title_map is None:
            title_map = {}

        try:
            # Get the root page itself
            time.sleep(self._rate_limit_delay)
            root_page = self._retry_on_rate_limit(self.client.pages.retrieve, page_id=root_id)
            root_title = _extract_page_title(root_page)
            if root_title:
                title_map[root_title] = root_id

            # Get all child blocks/pages
            blocks = self.get_blocks(root_id)

            for block in blocks:
                block_type = block.get("type")

                # Check if this block is a child page
                if block_type == "child_page":
                    child_id = block["id"]
                    child_title = block.get("child_page", {}).get("title", "")
                    if child_title:
                        title_map[child_title] = child_id
                    # Recurse into child page
                    self.list_all_pages_recursive(child_id, title_map)

                # Check if this block is a child database
                elif block_type == "child_database":
                    db_id = block["id"]
                    db_title = block.get("child_database", {}).get("title", "")
                    if db_title:
                        title_map[db_title] = db_id
                    # Get all pages (records) in the database
                    self._list_database_pages(db_id, title_map)

        except APIResponseError as e:
            logger.warning(f"Failed to traverse page {root_id}: {e}")

        return title_map
    
    def list_all_pages_batched(
        self, root_id: str, batch_size: int = 500, batch_callback=None
    ) -> dict[str, str]:
        """Recursively collect all pages with batched callbacks.
        
        Args:
            root_id: Root page ID to start traversal
            batch_size: Number of pages per batch before calling callback
            batch_callback: Optional callback function(batch_map) called every batch_size pages
        
        Returns:
            Complete dictionary mapping page title to page ID
        """
        all_pages: dict[str, str] = {}  # id -> title
        batch: dict[str, str] = {}      # id -> title
        
        def _collect_with_batching(page_id: str):
            nonlocal batch
            
            try:
                # Get the page itself
                time.sleep(self._rate_limit_delay)
                page = self._retry_on_rate_limit(self.client.pages.retrieve, page_id=page_id)
                page_title = _extract_page_title(page) or ""
                all_pages[page_id] = page_title
                batch[page_id] = page_title
                
                # Check if batch is full
                if len(batch) >= batch_size and batch_callback:
                    batch_callback(batch.copy())
                    batch.clear()
                
                # Get child blocks/pages
                blocks = self.get_blocks(page_id)
                
                for block in blocks:
                    block_type = block.get("type")
                    
                    if block_type == "child_page":
                        child_id = block["id"]
                        _collect_with_batching(child_id)
                    
                    elif block_type == "child_database":
                        db_id = block["id"]
                        db_title = block.get("child_database", {}).get("title", "")
                        all_pages[db_id] = db_title
                        batch[db_id] = db_title
                        
                        if len(batch) >= batch_size and batch_callback:
                            batch_callback(batch.copy())
                            batch.clear()
                        
                        self._list_database_pages_batched(db_id, all_pages, batch, batch_size, batch_callback)
            
            except APIResponseError as e:
                logger.warning(f"Failed to traverse page {page_id}: {e}")
        
        # Start collection
        _collect_with_batching(root_id)
        
        # Send final batch if any remaining
        if batch and batch_callback:
            batch_callback(batch.copy())
        
        return all_pages
    
    def list_all_accessible_pages_batched(
        self, batch_size: int = 500, batch_callback=None
    ) -> dict[str, str]:
        """Enumerate all pages and databases accessible to the integration.

        Uses the Notion search API (no query) to page through all accessible objects,
        then expands each database to include all of its records.

        Returns id->title map.
        """
        all_pages: dict[str, str] = {}
        batch: dict[str, str] = {}
        start_cursor = None
        while True:
            time.sleep(self._rate_limit_delay)
            try:
                resp = self._retry_on_rate_limit(self.client.search, start_cursor=start_cursor, page_size=100)
                results = resp.get("results", [])
                for obj in results:
                    obj_id = obj.get("id")
                    obj_type = obj.get("object")
                    title = ""
                    if obj_type == "page":
                        title = _extract_page_title(obj) or ""
                        all_pages[obj_id] = title
                        batch[obj_id] = title
                    elif obj_type == "database":
                        # capture database title and expand records
                        tarr = obj.get("title", [])
                        if tarr:
                            title = tarr[0].get("plain_text", "")
                        all_pages[obj_id] = title
                        batch[obj_id] = title
                        # expand database records
                        self._list_database_pages_batched(obj_id, all_pages, batch, batch_size, batch_callback)
                    # flush batch
                    if len(batch) >= batch_size and batch_callback:
                        batch_callback(batch.copy())
                        batch.clear()
                if not resp.get("has_more"):
                    break
                start_cursor = resp.get("next_cursor")
            except APIResponseError as e:
                logger.warning(f"Search pagination failed: {e}")
                break
        if batch and batch_callback:
            batch_callback(batch.copy())
        return all_pages

    def _list_database_pages_batched(
        self, database_id: str, all_pages: dict, batch: dict, batch_size: int, batch_callback
    ):
        """List database pages with batching support."""
        start_cursor = None
        
        while True:
            time.sleep(self._rate_limit_delay)
            
            headers = {
                "Authorization": f"Bearer {self._auth_token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_API_VERSION,
            }
            
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            
            def _query_db():
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if response.status_code != 200:
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get('message', response.text)
                    raise Exception(f"Database query failed (HTTP {response.status_code}): {error_msg}")
                return response
            
            try:
                response = self._retry_on_rate_limit(_query_db)
                
                data = response.json()
                pages = data.get("results", [])
                
                for page in pages:
                    page_id = page["id"]
                    page_title = _extract_page_title(page) or ""
                    all_pages[page_id] = page_title
                    batch[page_id] = page_title
                    
                    if len(batch) >= batch_size and batch_callback:
                        batch_callback(batch.copy())
                        batch.clear()
                    
                    # Recurse into database page (using batched version)
                    self._collect_pages_recursive_batched(page_id, all_pages, batch, batch_size, batch_callback)
                
                if not data.get("has_more"):
                    break
                
                start_cursor = data.get("next_cursor")
            except Exception as e:
                logger.warning(f"Failed to query database {database_id}: {e}")
                break
    
    def _collect_pages_recursive_batched(
        self, page_id: str, all_pages: dict, batch: dict, batch_size: int, batch_callback
    ):
        """Helper for recursive batched collection."""
        try:
            blocks = self.get_blocks(page_id)
            
            for block in blocks:
                block_type = block.get("type")
                
                if block_type == "child_page":
                    child_id = block["id"]
                    child_title = block.get("child_page", {}).get("title", "")
                    all_pages[child_id] = child_title
                    batch[child_id] = child_title
                    
                    if len(batch) >= batch_size and batch_callback:
                        batch_callback(batch.copy())
                        batch.clear()
                    
                    self._collect_pages_recursive_batched(child_id, all_pages, batch, batch_size, batch_callback)
                
                elif block_type == "child_database":
                    db_id = block["id"]
                    db_title = block.get("child_database", {}).get("title", "")
                    all_pages[db_id] = db_title
                    batch[db_id] = db_title
                    
                    if len(batch) >= batch_size and batch_callback:
                        batch_callback(batch.copy())
                        batch.clear()
                    
                    self._list_database_pages_batched(db_id, all_pages, batch, batch_size, batch_callback)
        
        except APIResponseError as e:
            logger.warning(f"Failed to traverse page {page_id}: {e}")

    def _list_database_pages(self, database_id: str, title_map: dict[str, str]):
        """List all pages in a database and add to title map.

        Args:
            database_id: Database ID
            title_map: Title map to add pages to
        """
        start_cursor = None

        while True:
            time.sleep(self._rate_limit_delay)

            # Use raw requests API because notion-client's data_sources.query() 
            # doesn't work with database IDs in the current API version
            headers = {
                "Authorization": f"Bearer {self._auth_token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_API_VERSION,
            }
            
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor

            def _query_db():
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if response.status_code != 200:
                    error_data = response.json() if response.text else {}
                    raise APIResponseError(
                        response.status_code,
                        error_data.get('message', response.text),
                        error_data
                    )
                return response

            try:
                response = self._retry_on_rate_limit(_query_db)
                
                data = response.json()
                pages = data.get("results", [])

                for page in pages:
                    page_id = page["id"]
                    page_title = _extract_page_title(page)
                    if page_title:
                        title_map[page_title] = page_id
                    # Recurse into database page to find any nested content
                    self.list_all_pages_recursive(page_id, title_map)

                # Check if there are more pages
                if not data.get("has_more"):
                    break

                start_cursor = data.get("next_cursor")
            except Exception as e:
                logger.warning(f"Failed to query database {database_id}: {e}")
                break

    def upload_file(self, file_data: bytes, filename: str, mime_type: str) -> str:
        """Upload file to Notion using Direct Upload API.

        Automatically uses multi-part upload for files >= 20MB, single-part for smaller files.
        Multi-part uploads split files into 20MB chunks, supporting files of any size.

        Uses the official Notion File Upload API:
        - Single-part (< 20MB): 2 steps (create + send)
        - Multi-part (>= 20MB): 3 steps (create + send parts + complete)

        Args:
            file_data: File binary data (any size supported)
            filename: File name
            mime_type: MIME type (e.g. 'image/png', 'application/pdf')

        Returns:
            File upload ID (to be used with type: file_upload)

        Raises:
            Exception: If upload fails or file type is not supported by Notion
        """
        file_size_bytes = len(file_data)
        file_size_mb = file_size_bytes / (1024 * 1024)
        
        # Determine upload mode based on file size
        # Use multi-part for files >= 20MB
        CHUNK_SIZE = 20 * 1024 * 1024  # 20MB chunks
        use_multipart = file_size_bytes >= CHUNK_SIZE
        
        if use_multipart:
            logger.debug(f"Using multi-part upload for {filename} ({file_size_mb:.1f} MB)")
            return self._upload_file_multipart(file_data, filename, mime_type)
        else:
            logger.debug(f"Using single-part upload for {filename} ({file_size_mb:.1f} MB)")
            return self._upload_file_singlepart(file_data, filename, mime_type)
    
    def _upload_file_singlepart(self, file_data: bytes, filename: str, mime_type: str) -> str:
        """Upload file using single-part mode (< 20MB).
        
        Args:
            file_data: File binary data
            filename: File name
            mime_type: MIME type
        
        Returns:
            File upload ID
        """
        
        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        # Step 1: Create file upload
        logger.debug(f"Creating single-part file upload for {filename} ({len(file_data)} bytes)")
        time.sleep(self._rate_limit_delay)
        
        def _create_upload():
            response = requests.post(
                "https://api.notion.com/v1/file_uploads",
                headers=headers,
                json={
                    "mode": "single_part",
                    "filename": filename,
                },
                timeout=30,
            )
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", response.text)
                raise Exception(f"Failed to create file upload (HTTP {response.status_code}): {error_msg}")
            return response
        
        create_response = self._retry_on_rate_limit(_create_upload)
        
        if create_response.status_code != 200:
            error_data = create_response.json() if create_response.text else {}
            error_msg = error_data.get("message", create_response.text)
            raise Exception(
                f"Failed to create file upload (HTTP {create_response.status_code}): {error_msg}"
            )
        
        create_data = create_response.json()
        upload_id = create_data["id"]
        logger.debug(f"  Upload ID: {upload_id}")
        
        # Step 2: Send file contents
        logger.debug(f"Sending file contents")
        time.sleep(self._rate_limit_delay)
        
        send_headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        send_response = requests.post(
            f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
            headers=send_headers,
            files={"file": (filename, file_data, mime_type)},
            timeout=300,  # 5 minutes
        )
        
        if send_response.status_code != 200:
            error_data = send_response.json() if send_response.text else {}
            error_msg = error_data.get("message", send_response.text)
            raise Exception(
                f"Failed to send file contents (HTTP {send_response.status_code}): {error_msg}"
            )
        
        logger.debug(f"  File uploaded successfully")
        return upload_id
    
    def _upload_file_multipart(self, file_data: bytes, filename: str, mime_type: str) -> str:
        """Upload file using multi-part mode (>= 20MB).
        
        Splits file into 20MB chunks and uploads each part separately.
        
        Args:
            file_data: File binary data
            filename: File name
            mime_type: MIME type
        
        Returns:
            File upload ID
        """
        CHUNK_SIZE = 20 * 1024 * 1024  # 20MB
        file_size = len(file_data)
        num_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE  # Ceiling division
        
        logger.debug(f"Multi-part upload: {file_size} bytes in {num_chunks} chunks")
        
        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        # Step 1: Create multi-part file upload
        logger.debug(f"Creating multi-part file upload for {filename}")
        time.sleep(self._rate_limit_delay)
        
        def _create_multipart():
            response = requests.post(
                "https://api.notion.com/v1/file_uploads",
                headers=headers,
                json={
                    "mode": "multi_part",
                    "filename": filename,
                    "number_of_parts": num_chunks,
                },
                timeout=30,
            )
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", response.text)
                raise Exception(f"Failed to create multi-part upload (HTTP {response.status_code}): {error_msg}")
            return response
        
        create_response = self._retry_on_rate_limit(_create_multipart)
        
        if create_response.status_code != 200:
            error_data = create_response.json() if create_response.text else {}
            error_msg = error_data.get("message", create_response.text)
            raise Exception(
                f"Failed to create multi-part upload (HTTP {create_response.status_code}): {error_msg}"
            )
        
        create_data = create_response.json()
        upload_id = create_data["id"]
        part_urls = create_data.get("part_urls", [])
        
        if len(part_urls) != num_chunks:
            raise Exception(
                f"Expected {num_chunks} part URLs, got {len(part_urls)}"
            )
        
        logger.debug(f"  Upload ID: {upload_id}, {num_chunks} parts")
        
        # Step 2: Upload each part
        send_headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        for part_num in range(num_chunks):
            start = part_num * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, file_size)
            chunk_data = file_data[start:end]
            
            logger.debug(f"  Uploading part {part_num + 1}/{num_chunks} ({len(chunk_data)} bytes)")
            time.sleep(self._rate_limit_delay)
            
            send_response = requests.post(
                f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
                headers=send_headers,
                json={"part_number": part_num},
                files={"file": (filename, chunk_data, mime_type)},
                timeout=300,
            )
            
            if send_response.status_code != 200:
                error_data = send_response.json() if send_response.text else {}
                error_msg = error_data.get("message", send_response.text)
                raise Exception(
                    f"Failed to send part {part_num + 1} (HTTP {send_response.status_code}): {error_msg}"
                )
        
        logger.debug(f"  All {num_chunks} parts uploaded")
        
        # Step 3: Complete the multi-part upload
        logger.debug(f"Completing multi-part upload")
        time.sleep(self._rate_limit_delay)
        
        def _complete_multipart():
            response = requests.post(
                f"https://api.notion.com/v1/file_uploads/{upload_id}/complete",
                headers=headers,
                json={},  # Empty body required
                timeout=30,
            )
            if response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("message", response.text)
                raise Exception(f"Failed to complete upload (HTTP {response.status_code}): {error_msg}")
            return response
        
        complete_response = self._retry_on_rate_limit(_complete_multipart)
        
        if complete_response.status_code != 200:
            error_data = complete_response.json() if complete_response.text else {}
            error_msg = error_data.get("message", complete_response.text)
            raise Exception(
                f"Failed to complete upload (HTTP {complete_response.status_code}): {error_msg}"
            )
        
        logger.debug(f"  Multi-part upload completed successfully")
        return upload_id


def create_notebook_database_schema() -> dict[str, Any]:
    """Create schema for notebook database.

    Returns:
        Properties schema dict for database creation
    """
    return {
        "Name": {"title": {}},  # Required: Every database needs a title property
        "Tags": {"multi_select": {}},
        "URL": {"url": {}},
        "Created": {"date": {}},
        "Updated": {"date": {}},
        "Partial Import": {"checkbox": {}},  # Flag for notes with import failures
    }


def note_to_database_properties(note, database_schema: dict[str, Any] | None = None, partial_import: bool = False) -> dict[str, Any]:
    """Convert EvernoteNote to database row properties.

    Args:
        note: EvernoteNote object
        database_schema: Optional existing database schema to adapt to
        partial_import: If True, marks the note as having import failures

    Returns:
        Properties dict for page creation in database
    """
    # If we have the database schema, use its actual property names
    if database_schema:
        return _adapt_to_database_schema(note, database_schema, partial_import)
    
    # Default properties for new database
    props = {
        "Name": {"title": [{"type": "text", "text": {"content": note.title}}]},
        "Created": {"date": {"start": note.created.isoformat()}},
        "Updated": {"date": {"start": note.updated.isoformat()}},
    }

    # Only add optional properties if they have values
    if note.url:
        props["URL"] = {"url": note.url}
    
    if note.tags:
        props["Tags"] = {"multi_select": [{"name": tag} for tag in note.tags]}
    
    # Mark as partial import if there were failures
    if partial_import:
        props["Partial Import"] = {"checkbox": True}

    return props


def _adapt_to_database_schema(note, database_schema: dict[str, Any], partial_import: bool = False) -> dict[str, Any]:
    """Adapt note properties to match existing database schema.

    Args:
        note: EvernoteNote object
        database_schema: Database properties schema from Notion API
                        Format: {"PropertyName": {"title": {}}} or {"PropertyName": {"type": "title", ...}}
        partial_import: If True, marks the note as having import failures

    Returns:
        Properties dict adapted to the database schema
    """
    props = {}
    
    # Find the title property (required)
    # Schema can be in two formats:
    # 1. From database creation: {"Name": {"title": {}}}
    # 2. From API retrieval: {"Name": {"type": "title", "title": {}}}
    title_prop_name = None
    for prop_name, prop_def in database_schema.items():
        # Check both formats
        if "title" in prop_def or prop_def.get("type") == "title":
            title_prop_name = prop_name
            break
    
    if title_prop_name:
        props[title_prop_name] = {"title": [{"type": "text", "text": {"content": note.title}}]}
    
    # Map other properties if they exist
    for prop_name, prop_def in database_schema.items():
        # Determine property type - check both schema formats
        prop_type = prop_def.get("type")
        if not prop_type:
            # Schema format from creation: {"url": {}}, {"multi_select": {}}, etc.
            for key in prop_def.keys():
                if key != "id":  # Skip the id field if present
                    prop_type = key
                    break
        
        if prop_type == "url" and note.url:
            props[prop_name] = {"url": note.url}
        elif prop_type == "date":
            # Map to Created or Updated based on name
            prop_name_lower = prop_name.lower()
            if "create" in prop_name_lower:
                props[prop_name] = {"date": {"start": note.created.isoformat()}}
            elif "update" in prop_name_lower or "edit" in prop_name_lower or "modify" in prop_name_lower:
                props[prop_name] = {"date": {"start": note.updated.isoformat()}}
        elif prop_type == "multi_select" and note.tags:
            props[prop_name] = {"multi_select": [{"name": tag} for tag in note.tags]}
        elif prop_type == "checkbox" and "partial" in prop_name.lower() and "import" in prop_name.lower():
            # Handle Partial Import checkbox
            props[prop_name] = {"checkbox": partial_import}
    
    return props


def _extract_page_title(page: dict[str, Any]) -> str:
    """Extract title from a page object.

    Args:
        page: Page object from Notion API

    Returns:
        Page title string, or empty string if not found
    """
    # Try to get title from properties (for pages in databases or regular pages)
    properties = page.get("properties", {})
    
    # Find the title property (could be named "Name", "title", etc.)
    for prop_name, prop_value in properties.items():
        prop_type = prop_value.get("type")
        if prop_type == "title":
            title_array = prop_value.get("title", [])
            if title_array and len(title_array) > 0:
                return title_array[0].get("plain_text", "")
    
    return ""
