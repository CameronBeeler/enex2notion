"""Track rejected/unsupported files during migration.

Maintains a record of files that couldn't be uploaded to Notion,
including the notebook, note, filename, reason, and extension.
"""
import csv
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RejectedFilesTracker:
    """Track files rejected during migration."""
    
    def __init__(self, output_file: Optional[Path] = None):
        """Initialize tracker.
        
        Args:
            output_file: Path to CSV file for rejected files report.
                        If None, tracking is disabled.
        """
        self.output_file = output_file
        self.rejected_files = []
        self.enabled = output_file is not None
        
    def add_rejected_file(
        self,
        notebook_name: str,
        note_title: str,
        filename: str,
        reason: str,
        file_extension: str = "",
    ):
        """Record a rejected file.
        
        Args:
            notebook_name: Name of the notebook (ENEX file)
            note_title: Title of the note containing the file
            filename: Name of the rejected file
            reason: Reason for rejection
            file_extension: File extension (e.g., '.pages')
        """
        if not self.enabled:
            return
            
        # Extract extension if not provided
        if not file_extension and "." in filename:
            file_extension = "." + filename.rsplit(".", 1)[1]
        
        self.rejected_files.append({
            "notebook": notebook_name,
            "note": note_title,
            "filename": filename,
            "extension": file_extension,
            "reason": reason,
        })
        
        logger.debug(
            f"Rejected file tracked: {notebook_name}/{note_title}/{filename} ({file_extension}): {reason}"
        )
    
    def save_report(self):
        """Save rejected files report to CSV."""
        if not self.enabled or not self.rejected_files:
            return
        
        try:
            # Ensure parent directory exists
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Check if file exists and needs header
            file_exists = self.output_file.exists()
            
            # Append to CSV report (preserves existing entries from previous runs)
            with open(self.output_file, "a", newline="", encoding="utf-8") as f:
                fieldnames = ["notebook", "note", "filename", "extension", "reason"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                # Write header only if file is new
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.rejected_files)
            
            logger.info(f"Rejected files report saved to: {self.output_file}")
            logger.info(f"  Total rejected files this run: {len(self.rejected_files)}")
            
        except Exception as e:
            logger.error(f"Failed to save rejected files report: {e}")
    
    def get_count(self) -> int:
        """Get count of rejected files."""
        return len(self.rejected_files)
    
    def get_summary(self) -> dict[str, int]:
        """Get summary of rejected files by extension.
        
        Returns:
            Dict mapping extension to count
        """
        summary = {}
        for entry in self.rejected_files:
            ext = entry["extension"]
            summary[ext] = summary.get(ext, 0) + 1
        return summary
