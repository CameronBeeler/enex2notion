"""Track page inventory during link resolution with batched updates to Notion."""
import logging
from typing import Any

from enex2notion.notion_api_wrapper import NotionAPIWrapper

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


class PageInventoryTracker:
    """Tracks page inventory in a Notion page with batched updates."""
    
    def __init__(self, wrapper: NotionAPIWrapper, root_id: str):
        """Initialize inventory tracker.
        
        Args:
            wrapper: NotionAPIWrapper instance
            root_id: Root page ID (where inventory page will be created)
        """
        self.wrapper = wrapper
        self.root_id = root_id
        self.inventory_page_id = None
        self.page_count = 0
    
    def create_inventory_page(self) -> str:
        """Create or find the 'Notion Pages Inventory' page.
        
        Returns:
            Page ID of the inventory page
        """
        # Check if inventory page already exists
        blocks = self.wrapper.get_blocks(self.root_id)
        
        for block in blocks:
            if block.get("type") == "child_page":
                title = block.get("child_page", {}).get("title", "")
                if title == "Notion Pages Inventory":
                    self.inventory_page_id = block["id"]
                    logger.info(f"Found existing inventory page: {self.inventory_page_id}")
                    return self.inventory_page_id
        
        # Create new inventory page
        logger.info("Creating new 'Notion Pages Inventory' page...")
        
        page = self.wrapper.create_page(self.root_id, "Notion Pages Inventory")
        self.inventory_page_id = page["id"]
        
        # Add initial structure
        children = [
            {
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"type": "text", "text": {"content": "Page Collection Progress"}}]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "Status: "}}
                    ]
                }
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Collected Pages (Alphabetical)"}}]
                }
            }
        ]
        
        self.wrapper.append_blocks(self.inventory_page_id, children)
        
        logger.info(f"Created inventory page: {self.inventory_page_id}")
        return self.inventory_page_id
    
    def append_page_batch(self, page_map: dict[str, str], processed_pages: set[str]):
        """Append a batch of pages to the inventory.
        
        Args:
            page_map: Dictionary of page titles to IDs for this batch
            processed_pages: Set of page IDs that have been processed (no evernote links)
        """
        if not self.inventory_page_id:
            self.create_inventory_page()
        
        # Preserve discovery order as provided in page_map
        # Create numbered list blocks
        blocks = []
        for page_id, title in page_map.items():
            self.page_count += 1
            
            # Check if processed
            status = "✓" if page_id in processed_pages else ""
            
            # Create link to the page
            page_url = f"https://notion.so/{page_id.replace('-', '')}"
            
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [
                        {"text": {"content": f"{title}", "link": {"url": page_url}}},
                        {"text": {"content": f" {status}" if status else ""}}
                    ]
                }
            })
        
        # Append blocks in chunks (max 100 per request)
        chunk_size = 100
        for i in range(0, len(blocks), chunk_size):
            chunk = blocks[i:i + chunk_size]
            try:
                self.wrapper.append_blocks(self.inventory_page_id, chunk)
                logger.info(f"Appended {len(chunk)} pages to inventory (total: {self.page_count})")
            except Exception as e:
                logger.error(f"Failed to append blocks to inventory: {e}")
                raise
    
    def update_status(self, status_text: str):
        """Update the status paragraph at the top of the inventory page.
        
        Args:
            status_text: Status message to display
        """
        if not self.inventory_page_id:
            return
        
        try:
            # Get all blocks to find the status paragraph (2nd block)
            blocks = self.wrapper.get_blocks(self.inventory_page_id)
            
            if len(blocks) >= 2 and blocks[1].get("type") == "paragraph":
                status_block_id = blocks[1]["id"]
                
                self.wrapper.update_block(status_block_id, {
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": "Status: "}},
                            {"type": "text", "text": {"content": status_text}, "annotations": {"bold": True}}
                        ]
                    }
                })
                
                logger.debug(f"Updated inventory status: {status_text}")
        except Exception as e:
            logger.warning(f"Failed to update inventory status: {e}")
    
    def finalize(self, total_pages: int, total_links: int, links_matched: int):
        """Update final status with summary statistics.
        
        Args:
            total_pages: Total number of pages scanned
            total_links: Total evernote links found
            links_matched: Number of links successfully matched
        """
        status = (
            f"✓ Complete - Scanned {total_pages} pages, "
            f"found {total_links} evernote:// links, "
            f"matched {links_matched}"
        )
        self.update_status(status)
        logger.info(f"Inventory finalized: {status}")
