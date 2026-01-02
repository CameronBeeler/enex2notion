# Official Notion API Migration Guide

**Status**: Requirements updated, implementation pending  
**Goal**: Migrate from `notion-vzhd1701-fork` (token_v2) to `notion-client` (Integration tokens)

## Overview

This guide provides a complete, step-by-step migration from the unofficial Notion API to the official Notion API with Integration token authentication.

**Estimated Time**: 2-3 hours  
**Risk Level**: High (touches all upload functionality)  
**Complexity**: Major architectural change

---

## Prerequisites

Before starting:
1. ✅ **Python 3.12+** installed
2. ✅ **Git** for version control (recommended for rollback)
3. ✅ **Notion account** with admin access
4. ✅ **Test ENEX file** (10 records recommended)
5. ✅ **Backup current code**: `git commit -am "Pre-migration checkpoint"`

---

## Phase 1: Install New Dependencies

### Step 1.1: Uninstall Old Package

```bash
pip uninstall notion-vzhd1701-fork -y
```

### Step 1.2: Install Official Client

```bash
pip install notion-client>=2.7.0
```

### Step 1.3: Verify Installation

```bash
python -c "from notion_client import Client; print('✅ notion-client installed')"
```

**Expected Output**: `✅ notion-client installed`

---

## Phase 2: Create Integration Token

### Step 2.1: Create Notion Integration

1. Go to: https://www.notion.com/my-integrations
2. Click **"+ New integration"**
3. Fill in:
   - **Name**: `enex2notion` (or your preference)
   - **Associated workspace**: Select your workspace
   - **Type**: Internal integration
4. Click **"Submit"**
5. **Copy the Integration Token** (starts with `secret_`)

⚠️ **IMPORTANT**: Save this token securely. You'll need it for every import.

### Step 2.2: Share Pages with Integration

The Integration needs access to pages/databases:

1. Open Notion
2. Navigate to page where imports will go
3. Click **"..."** menu → **"Add connections"**
4. Select your **"enex2notion"** integration
5. Click **"Confirm"**

**Note**: You must share the root page. The tool will create child pages/databases under it.

### Step 2.3: Test Integration Token

```bash
export NOTION_TOKEN="your_integration_token_here"
python -c "
from notion_client import Client
client = Client(auth='$NOTION_TOKEN')
result = client.users.me()
print(f'✅ Authenticated as: {result[\"name\"]}')
"
```

---

## Phase 3: Create API Wrapper Module

### Step 3.1: Create `enex2notion/notion_api_wrapper.py`

This module abstracts the official API and provides compatibility with existing code.

**File Location**: `enex2notion/notion_api_wrapper.py`

**Contents**:

```python
"""Wrapper for official Notion API.

Provides abstraction layer for page/database/block operations
using the official notion-client package with Integration tokens.
"""
import logging
import time
from typing import Any

import requests
from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)


class NotionAPIWrapper:
    """Wrapper around official Notion API client."""

    def __init__(self, auth_token: str):
        """Initialize with Integration token.

        Args:
            auth_token: Notion Integration token (starts with secret_)
        """
        self.client = Client(auth=auth_token)
        self._rate_limit_delay = 0.35  # ~3 requests/second

    def search_pages(self, title: str) -> list[dict[str, Any]]:
        """Search for pages by title.

        Args:
            title: Page title to search for

        Returns:
            List of matching page objects
        """
        try:
            response = self.client.search(
                filter={"property": "object", "value": "page"}, query=title
            )
            return [
                r
                for r in response.get("results", [])
                if r.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text") == title
            ]
        except APIResponseError as e:
            logger.error(f"Search failed: {e}")
            return []

    def create_page(self, parent_id: str, title: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a new page.

        Args:
            parent_id: Parent page/database ID
            title: Page title
            properties: Additional properties (for database pages)

        Returns:
            Created page object
        """
        page_data = {
            "parent": {"page_id": parent_id} if not properties else {"database_id": parent_id},
            "properties": {
                "title": {
                    "title": [{"type": "text", "text": {"content": title}}]
                }
            },
        }

        if properties:
            page_data["properties"].update(properties)

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
        """
        database_data = {
            "parent": {"page_id": parent_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties_schema,
        }

        time.sleep(self._rate_limit_delay)
        return self.client.databases.create(**database_data)

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

    def upload_file(self, file_data: bytes, filename: str, mime_type: str) -> str:
        """Upload file to Notion via AWS S3.

        Note: Official API doesn't directly support file uploads to blocks yet.
        This is a placeholder for future implementation.

        Args:
            file_data: File binary data
            filename: File name
            mime_type: MIME type

        Returns:
            File URL

        Raises:
            NotImplementedError: File upload not yet supported in official API
        """
        # TODO: Implement when official API adds file upload support
        # For now, files must be uploaded externally and linked
        raise NotImplementedError(
            "Direct file upload not yet supported by official Notion API. "
            "Files must be hosted externally and linked."
        )


def create_notebook_database_schema() -> dict[str, Any]:
    """Create schema for notebook database.

    Returns:
        Properties schema dict for database creation
    """
    return {
        "Tags": {"multi_select": {}},
        "URL": {"url": {}},
        "Created": {"date": {}},
        "Updated": {"date": {}},
    }


def note_to_database_properties(note) -> dict[str, Any]:
    """Convert EvernoteNote to database row properties.

    Args:
        note: EvernoteNote object

    Returns:
        Properties dict for page creation in database
    """
    props = {
        "title": {"title": [{"type": "text", "text": {"content": note.title}}]},
        "URL": {"url": note.url} if note.url else {"url": None},
        "Created": {"date": {"start": note.created.isoformat()}},
        "Updated": {"date": {"start": note.updated.isoformat()}},
    }

    if note.tags:
        props["Tags"] = {"multi_select": [{"name": tag} for tag in note.tags]}

    return props
```

### Step 3.2: Test the Wrapper

```bash
python -c "
from enex2notion.notion_api_wrapper import NotionAPIWrapper
wrapper = NotionAPIWrapper(auth_token='your_token_here')
print('✅ Wrapper module created successfully')
"
```

---

## Phase 4: Update Authentication Module

### Step 4.1: Backup Current File

```bash
cp enex2notion/cli_notion.py enex2notion/cli_notion.py.backup
```

### Step 4.2: Replace `enex2notion/cli_notion.py`

**New contents**:

```python
import logging
import sys

from notion_client import Client
from notion_client.errors import APIResponseError

from enex2notion.notion_api_wrapper import NotionAPIWrapper
from enex2notion.utils_exceptions import BadTokenException

logger = logging.getLogger(__name__)


def get_root(token, name):
    """Get or create root page for imports.

    Args:
        token: Notion Integration token
        name: Root page name

    Returns:
        NotionAPIWrapper instance configured with root page, or None for dry run
    """
    if not token:
        logger.warning("No token provided, dry run mode. Nothing will be uploaded to Notion!")
        return None

    try:
        wrapper = get_notion_wrapper(token)
    except BadTokenException:
        logger.error("Invalid Integration token provided!")
        logger.error("Create an Integration at: https://www.notion.com/my-integrations")
        sys.exit(1)

    return get_import_root(wrapper, name)


def get_notion_wrapper(token):
    """Initialize Notion API wrapper with Integration token.

    Args:
        token: Notion Integration token

    Returns:
        NotionAPIWrapper instance

    Raises:
        BadTokenException: If token is invalid
    """
    try:
        wrapper = NotionAPIWrapper(auth_token=token)
        # Test authentication
        wrapper.client.users.me()
        return wrapper
    except APIResponseError as e:
        if e.status == 401:
            raise BadTokenException
        raise


def get_import_root(wrapper, title):
    """Get or create root page for imports.

    Args:
        wrapper: NotionAPIWrapper instance
        title: Page title to find or create

    Returns:
        Root page ID as string
    """
    # Search for existing page
    pages = wrapper.search_pages(title)

    if pages:
        page_id = pages[0]["id"]
        logger.info(f"'{title}' page found: {page_id}")
        return page_id

    # Page not found - need to create in shared space
    # User must have already shared a page with the Integration
    logger.error(f"Root page '{title}' not found and cannot be auto-created.")
    logger.error("Please:")
    logger.error("  1. Create a page in Notion named '{title}'")
    logger.error("  2. Share it with your Integration")
    logger.error("  3. Run this command again")
    sys.exit(1)
```

### Step 4.3: Test Authentication

```bash
python -c "
from enex2notion.cli_notion import get_root
root = get_root('your_token_here', 'Test Page')
print(f'✅ Authentication working, root: {root}')
"
```

---

## Phase 5: Update Upload Modes

### Step 5.1: Update `enex2notion/enex_uploader_modes.py`

Replace the entire file with official API implementation:

```python
from enex2notion.notion_api_wrapper import create_notebook_database_schema
from enex2notion.utils_exceptions import NoteUploadFailException

import logging

logger = logging.getLogger(__name__)


def get_notebook_page(wrapper, root_page_id, title):
    """Get or create notebook page.

    Args:
        wrapper: NotionAPIWrapper instance
        root_page_id: Parent page ID
        title: Notebook title

    Returns:
        Page ID string
    """
    try:
        return _get_notebook_page(wrapper, root_page_id, title)
    except Exception as e:
        raise NoteUploadFailException from e


def _get_notebook_page(wrapper, root_page_id, title):
    """Internal: Get or create notebook page."""
    # Search for existing page
    pages = wrapper.search_pages(title)

    for page in pages:
        if page.get("parent", {}).get("page_id") == root_page_id:
            logger.info(f"Found existing notebook page: {title}")
            return page["id"]

    # Create new page
    logger.info(f"Creating new notebook page: {title}")
    page = wrapper.create_page(parent_id=root_page_id, title=title)
    return page["id"]


def get_notebook_database(wrapper, root_page_id, title):
    """Get or create notebook database.

    Args:
        wrapper: NotionAPIWrapper instance
        root_page_id: Parent page ID
        title: Database title

    Returns:
        Database ID string
    """
    try:
        return _get_notebook_database(wrapper, root_page_id, title)
    except Exception as e:
        raise NoteUploadFailException from e


def _get_notebook_database(wrapper, root_page_id, title):
    """Internal: Get or create notebook database."""
    # Search for existing database
    # Note: Official API doesn't have direct database search by title
    # We'll search pages and filter for databases
    pages = wrapper.search_pages(title)

    for page in pages:
        if page.get("object") == "database" and page.get("parent", {}).get("page_id") == root_page_id:
            logger.info(f"Found existing notebook database: {title}")
            return page["id"]

    # Create new database
    logger.info(f"Creating new notebook database: {title}")
    schema = create_notebook_database_schema()
    database = wrapper.create_database(parent_id=root_page_id, title=title, properties_schema=schema)
    return database["id"]
```

---

## Phase 6: Update Block Upload (CRITICAL)

This is the most complex part. The official API uses completely different block structures.

### Step 6.1: Create Block Converter

Create `enex2notion/notion_block_converter.py`:

```python
"""Convert internal block representations to official Notion API format."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def convert_block_to_api_format(block) -> dict[str, Any] | None:
    """Convert internal block to official API format.

    Args:
        block: Internal NotionBaseBlock instance

    Returns:
        Block dict in official API format, or None if unsupported
    """
    block_type = getattr(block, "type", None)
    
    if block_type is None:
        logger.warning(f"Block has no type: {block}")
        return None

    # Map block types to conversion functions
    converters = {
        "paragraph": _convert_text_block,
        "heading_1": _convert_heading,
        "heading_2": _convert_heading,
        "heading_3": _convert_heading,
        "bulleted_list_item": _convert_list_item,
        "numbered_list_item": _convert_list_item,
        "to_do": _convert_todo,
        "divider": _convert_divider,
        # Add more as needed
    }

    converter = converters.get(block_type.__name__ if hasattr(block_type, "__name__") else str(block_type))
    
    if converter:
        return converter(block)
    
    logger.warning(f"Unsupported block type: {block_type}")
    return None


def _convert_text_block(block) -> dict[str, Any]:
    """Convert text block to paragraph."""
    text_prop = getattr(block, "text_prop", None)
    
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else []
        }
    }


def _convert_heading(block) -> dict[str, Any]:
    """Convert heading block."""
    block_type_name = block.type.__name__.lower()
    level = block_type_name.split("_")[-1]  # Extract number from heading_1, etc.
    
    text_prop = getattr(block, "text_prop", None)
    
    return {
        "type": f"heading_{level}",
        f"heading_{level}": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else []
        }
    }


def _convert_list_item(block) -> dict[str, Any]:
    """Convert list item block."""
    block_type_name = block.type.__name__.lower()
    
    text_prop = getattr(block, "text_prop", None)
    
    return {
        "type": block_type_name,
        block_type_name: {
            "rich_text": _convert_text_prop(text_prop) if text_prop else []
        }
    }


def _convert_todo(block) -> dict[str, Any]:
    """Convert todo block."""
    text_prop = getattr(block, "text_prop", None)
    checked = getattr(block, "checked", False)
    
    return {
        "type": "to_do",
        "to_do": {
            "rich_text": _convert_text_prop(text_prop) if text_prop else [],
            "checked": checked
        }
    }


def _convert_divider(block) -> dict[str, Any]:
    """Convert divider block."""
    return {"type": "divider", "divider": {}}


def _convert_text_prop(text_prop) -> list[dict[str, Any]]:
    """Convert text property to rich_text array.

    Args:
        text_prop: TextProp object with text and properties

    Returns:
        List of rich_text objects
    """
    if not text_prop or not hasattr(text_prop, "text"):
        return []
    
    # Simple implementation - just plain text for now
    # TODO: Add support for formatting (bold, italic, etc.)
    return [{
        "type": "text",
        "text": {"content": text_prop.text[:2000]}  # Notion limit: 2000 chars
    }]
```

**⚠️ IMPORTANT**: This converter is simplified. You'll need to expand it to handle:
- Text formatting (bold, italic, underline, strikethrough)
- Colors
- Links
- Tables
- Files/images
- Code blocks
- Callouts
- Quotes

---

## Phase 7: Update Uploader (CRITICAL)

### Step 7.1: Update `enex2notion/enex_uploader.py`

Replace block upload logic:

```python
import logging

from tqdm import tqdm

from enex2notion.enex_types import EvernoteNote
from enex2notion.notion_block_converter import convert_block_to_api_format
from enex2notion.notion_api_wrapper import note_to_database_properties
from enex2notion.utils_exceptions import NoteUploadFailException

logger = logging.getLogger(__name__)

PROGRESS_BAR_WIDTH = 80


def upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, keep_failed, is_database=False):
    """Upload note to Notion using official API.

    Args:
        wrapper: NotionAPIWrapper instance
        root_id: Parent page/database ID
        note: EvernoteNote object
        note_blocks: List of block objects
        keep_failed: Whether to keep failed uploads
        is_database: True if uploading to database, False for page
    """
    try:
        _upload_note(wrapper, root_id, note, note_blocks, keep_failed, is_database)
    except Exception as e:
        raise NoteUploadFailException from e


def _upload_note(wrapper, root_id, note: EvernoteNote, note_blocks, keep_failed, is_database):
    """Internal: Upload note."""
    logger.debug(f"Creating new page for note '{note.title}'")

    # Create page
    if is_database:
        properties = note_to_database_properties(note)
        new_page = wrapper.create_page(parent_id=root_id, title=note.title, properties=properties)
    else:
        new_page = wrapper.create_page(parent_id=root_id, title=note.title)

    page_id = new_page["id"]

    # Convert blocks to API format
    api_blocks = []
    for block in note_blocks:
        converted = convert_block_to_api_format(block)
        if converted:
            api_blocks.append(converted)

    # Upload blocks in batches
    progress_iter = tqdm(
        iterable=range(0, len(api_blocks), 100),
        total=(len(api_blocks) + 99) // 100,
        unit="batch",
        leave=False,
        ncols=PROGRESS_BAR_WIDTH,
    )

    try:
        for start_idx in progress_iter:
            batch = api_blocks[start_idx : start_idx + 100]
            wrapper.append_blocks(block_id=page_id, children=batch)

    except Exception as e:
        if not keep_failed:
            # Archive the page (official API doesn't have remove)
            try:
                wrapper.client.pages.update(page_id=page_id, archived=True)
            except:
                pass
        raise

    logger.debug(f"Successfully uploaded note '{note.title}' with {len(api_blocks)} blocks")
```

---

## Phase 8: Update CLI Arguments

### Step 8.1: Update help text in `enex2notion/cli_args.py`

Find the `--token` definition and update:

```python
"--token": {
    "help": (
        "Notion Integration token (create at https://www.notion.com/my-integrations) "
        "[NEEDED FOR UPLOAD]"
    ),
},
```

---

## Phase 9: Update Main CLI

### Step 9.1: Minor updates to `enex2notion/cli_upload.py`

Update the uploader method calls to pass the wrapper and mode information:

Look for `upload_note` calls and ensure they pass:
- `wrapper` instead of `root` 
- `is_database=(self.mode == "DB")`

---

## Phase 10: Testing

### Step 10.1: Create Integration Token

Follow Phase 2 steps to create token.

### Step 10.2: Test Authentication

```bash
python -m enex2notion --help
# Verify help text shows Integration token instructions
```

### Step 10.3: Test Dry Run

```bash
python -m enex2notion your_test.enex --verbose
# Should parse successfully without uploading
```

### Step 10.4: Create Root Page

1. In Notion, create page named "Evernote ENEX Import"
2. Share with your Integration
3. Get page URL

### Step 10.5: Test Upload

```bash
python -m enex2notion your_test.enex \
  --token secret_YOUR_TOKEN_HERE \
  --verbose \
  --summary summary.txt
```

### Step 10.6: Verify Results

- Check Notion for imported notes
- Review summary report
- Check unimported directory for failures

---

## Troubleshooting

### Error: "Invalid Integration token"
- Verify token starts with `secret_`
- Recreate Integration if needed
- Check token wasn't truncated when copying

### Error: "Root page not found"
- Create page in Notion manually
- Share page with Integration (Add connections)
- Use exact page name in --root-page argument

### Error: "Block type not supported"
- Check `notion_block_converter.py`
- Add converter for missing block type
- Check official API docs for supported blocks

### Files Not Uploading
- Official API doesn't support direct file uploads yet
- Files must be hosted externally and linked
- Consider implementing external file hosting

### Rate Limit Errors
- Wrapper includes automatic delays
- For large imports, errors may still occur
- Implement exponential backoff if needed

---

## Rollback Instructions

If migration fails:

```bash
# Restore old code
git checkout enex2notion/cli_notion.py
git checkout enex2notion/enex_uploader.py
git checkout enex2notion/enex_uploader_modes.py
git checkout requirements.txt

# Reinstall old dependencies
pip install notion-vzhd1701-fork==0.0.37

# Delete new files
rm enex2notion/notion_api_wrapper.py
rm enex2notion/notion_block_converter.py
```

---

## Post-Migration Checklist

- [ ] All tests passing
- [ ] 10-record test successful
- [ ] Documentation updated
- [ ] Old token_v2 references removed
- [ ] Error messages reference Integration tokens
- [ ] README has Integration setup instructions
- [ ] Migration guide reviewed

---

## Next Steps

After successful migration:
1. Update README with Integration token instructions
2. Add examples with Integration tokens
3. Update WARP.md to mark migration complete
4. Test with larger ENEX files
5. Implement file upload workaround if needed
6. Optimize block conversion for all types
7. Add support for all Notion block types

---

## Support

If you encounter issues:
1. Check official Notion API docs: https://developers.notion.com/
2. Check notion-client docs: https://github.com/ramnes/notion-sdk-py
3. Review error messages carefully
4. Test with minimal ENEX file first
5. Verify Integration token and permissions

---

**Good luck with the migration! 🚀**
