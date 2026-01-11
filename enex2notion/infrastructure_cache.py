"""Infrastructure cache for tracking Notion page and database IDs.

This cache file helps avoid relying on Notion's search index which can lag.
It stores IDs for maintenance databases and pages that are reused across imports.
"""
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class InfrastructureCache:
    """Manages the enex2notion.txt cache file for infrastructure IDs."""
    
    def __init__(self, working_dir: Path):
        """Initialize the cache.
        
        Args:
            working_dir: Directory where enex2notion.txt will be stored
        """
        self.cache_file = working_dir / "enex2notion.txt"
        self.exceptions_page_id: Optional[str] = None
        self.databases: Dict[str, str] = {}  # database_name -> database_id
        
        # Ensure working directory exists
        working_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing cache
        self._load()
    
    def _load(self):
        """Load cache from file."""
        if not self.cache_file.exists():
            logger.debug(f"No cache file found at {self.cache_file}")
            return
        
        try:
            with open(self.cache_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse format: name<TAB>id
                    parts = line.split('\t')
                    if len(parts) != 2:
                        # Try old format with space (for backwards compat)
                        parts = line.split(maxsplit=1)
                        if len(parts) == 2:
                            parts[0] = parts[0].strip('"')  # Remove quotes from old format
                    
                    if len(parts) != 2:
                        continue
                    
                    name = parts[0]
                    item_id = parts[1]
                    
                    if name == "Exceptions":
                        self.exceptions_page_id = item_id
                        logger.debug(f"Loaded Exceptions page ID from cache: {item_id}")
                    else:
                        self.databases[name] = item_id
                        logger.debug(f"Loaded database '{name}' ID from cache: {item_id}")
        except Exception as e:
            logger.warning(f"Failed to load cache file: {e}")
    
    def _save(self):
        """Save cache to file."""
        try:
            with open(self.cache_file, 'w') as f:
                f.write("# enex2notion infrastructure cache\n")
                f.write("# Format: name<TAB>id\n")
                f.write("# Do not edit manually\n\n")
                
                if self.exceptions_page_id:
                    f.write(f'Exceptions\t{self.exceptions_page_id}\n')
                
                for name, db_id in sorted(self.databases.items()):
                    f.write(f'{name}\t{db_id}\n')
            
            logger.debug(f"Saved cache to {self.cache_file}")
        except Exception as e:
            logger.warning(f"Failed to save cache file: {e}")
    
    def get_exceptions_page_id(self) -> Optional[str]:
        """Get the cached Exceptions page ID."""
        return self.exceptions_page_id
    
    def set_exceptions_page_id(self, page_id: str):
        """Set the Exceptions page ID and save."""
        if self.exceptions_page_id != page_id:
            self.exceptions_page_id = page_id
            self._save()
            logger.debug(f"Cached Exceptions page ID: {page_id}")
    
    def get_database_id(self, database_name: str) -> Optional[str]:
        """Get a cached database ID by name.
        
        Args:
            database_name: Name of the database (e.g., "User Action Required")
            
        Returns:
            Database ID if found, None otherwise
        """
        return self.databases.get(database_name)
    
    def set_database_id(self, database_name: str, database_id: str):
        """Set a database ID and save.
        
        Args:
            database_name: Name of the database (e.g., "User Action Required")
            database_id: Database ID to cache
        """
        if self.databases.get(database_name) != database_id:
            self.databases[database_name] = database_id
            self._save()
            logger.debug(f"Cached database '{database_name}' ID: {database_id}")
    
    def clear_database(self, database_name: str):
        """Remove a database from cache.
        
        Args:
            database_name: Name of the database to remove
        """
        if database_name in self.databases:
            del self.databases[database_name]
            self._save()
            logger.debug(f"Removed database '{database_name}' from cache")
