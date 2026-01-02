"""Wrapper for official Notion API.

Provides abstraction layer for page/database/block operations
using the official notion-client package with Integration tokens.

Note: Database creation uses raw requests due to notion-client 2.7.0 bug
that silently drops the 'properties' parameter.
"""
import logging
import time
from typing import Any
import requests

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)

NOTION_API_VERSION = "2022-06-28"


class NotionAPIWrapper:
    """Wrapper around official Notion API client."""

    def __init__(self, auth_token: str):
        """Initialize with Integration token.

        Args:
            auth_token: Notion Integration token (starts with secret_)
        """
        self.client = Client(auth=auth_token)
        self._auth_token = auth_token  # Store for raw API calls
        self._rate_limit_delay = 0.35  # ~3 requests/second

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
            response = self.client.search(query=title)
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

    def create_page(self, parent_id: str, title: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a new page.

        Args:
            parent_id: Parent page/database ID
            title: Page title
            properties: Additional properties (for database pages - will replace default)

        Returns:
            Created page object
        """
        if properties:
            # Creating a page in a database
            page_data = {
                "parent": {"type": "database_id", "database_id": parent_id},
                "properties": properties,
            }
        else:
            # Creating a regular page
            page_data = {
                "parent": {"type": "page_id", "page_id": parent_id},
                "properties": {
                    "title": {
                        "title": [{"type": "text", "text": {"content": title}}]
                    }
                },
            }

        time.sleep(self._rate_limit_delay)
        return self.client.pages.create(**page_data)

    def create_database(
        self, parent_id: str, title: str, properties_schema: dict[str, Any]
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
        database_data = {
            "parent": {"type": "page_id", "page_id": parent_id},
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
        
        response = requests.post(
            "https://api.notion.com/v1/databases",
            headers=headers,
            json=database_data,
            timeout=30,
        )
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            raise APIResponseError(response.status_code, error_data.get("message", response.text), error_data)
        
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
                response = self.client.blocks.children.append(block_id=block_id, children=batch)
                created_blocks.extend(response.get("results", []))
                logger.debug(f"Appended {len(batch)} blocks to {block_id}")
            except APIResponseError as e:
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
        return self.client.blocks.retrieve(block_id=block_id)

    def get_database(self, database_id: str) -> dict[str, Any]:
        """Retrieve a database and its schema.

        Args:
            database_id: Database ID

        Returns:
            Database object with properties schema
        """
        time.sleep(self._rate_limit_delay)
        return self.client.databases.retrieve(database_id=database_id)

    def upload_file(self, file_data: bytes, filename: str, mime_type: str) -> str:
        """Upload file to Notion using Direct Upload API.

        Uses the official Notion File Upload API (3-step process):
        1. Create file upload (get upload URL)
        2. Send file contents
        3. Complete upload

        Args:
            file_data: File binary data
            filename: File name
            mime_type: MIME type (e.g. 'image/png')

        Returns:
            File upload ID (to be used with type: file_upload)

        Raises:
            APIResponseError: If upload fails
        """
        headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        }
        
        # Step 1: Create file upload
        logger.debug(f"Creating file upload for {filename} ({len(file_data)} bytes)")
        time.sleep(self._rate_limit_delay)
        
        create_response = requests.post(
            "https://api.notion.com/v1/file_uploads",
            headers=headers,
            json={
                "mode": "single_part",  # For files < 20MB
                "filename": filename,
            },
            timeout=30,
        )
        
        if create_response.status_code != 200:
            error_data = create_response.json() if create_response.text else {}
            error_msg = error_data.get("message", create_response.text)
            raise Exception(
                f"Failed to create file upload (HTTP {create_response.status_code}): {error_msg}"
            )
        
        create_data = create_response.json()
        upload_id = create_data["id"]
        upload_url = create_data["upload_url"]
        logger.debug(f"  Upload ID: {upload_id}")
        
        # Step 2: Send file contents
        # Use the /send endpoint which requires Authorization header
        logger.debug(f"Sending file contents")
        time.sleep(self._rate_limit_delay)
        
        send_headers = {
            "Authorization": f"Bearer {self._auth_token}",
            "Notion-Version": NOTION_API_VERSION,
            # NOTE: Do NOT set Content-Type - let requests handle multipart/form-data
        }
        
        send_response = requests.post(
            f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
            headers=send_headers,
            files={"file": (filename, file_data, mime_type)},
            timeout=60,
        )
        
        if send_response.status_code != 200:
            error_data = send_response.json() if send_response.text else {}
            error_msg = error_data.get("message", send_response.text)
            raise Exception(
                f"Failed to send file contents (HTTP {send_response.status_code}): {error_msg}"
            )
        
        logger.debug(f"  File sent successfully, status: uploaded")
        
        # NOTE: For single_part mode, the file is automatically marked as "uploaded"
        # after the send step. No need to call /complete endpoint.
        # The complete endpoint is only used for multi_part uploads.
        
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
    }


def note_to_database_properties(note, database_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert EvernoteNote to database row properties.

    Args:
        note: EvernoteNote object
        database_schema: Optional existing database schema to adapt to

    Returns:
        Properties dict for page creation in database
    """
    # If we have the database schema, use its actual property names
    if database_schema:
        return _adapt_to_database_schema(note, database_schema)
    
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

    return props


def _adapt_to_database_schema(note, database_schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt note properties to match existing database schema.

    Args:
        note: EvernoteNote object
        database_schema: Database properties schema from Notion API
                        Format: {"PropertyName": {"title": {}}} or {"PropertyName": {"type": "title", ...}}

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
    
    return props
