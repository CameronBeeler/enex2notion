"""Report generation for link resolution results."""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enex2notion.link_resolver import LinkReference

logger = logging.getLogger(__name__)


@dataclass
class LinkResolutionStats:
    """Statistics for link resolution process.
    
    Attributes:
        total_pages_scanned: Total number of pages scanned
        pages_with_links: Number of pages containing evernote:// links
        total_links_found: Total evernote:// links found
        links_matched: Number of links successfully matched to pages
        links_unmatched: Number of links that couldn't be matched
        links_updated: Number of links actually updated (if not dry-run)
    """
    total_pages_scanned: int = 0
    pages_with_links: int = 0
    total_links_found: int = 0
    links_matched: int = 0
    links_unmatched: int = 0
    links_updated: int = 0


@dataclass
class MatchedLink:
    """A successfully matched link.
    
    Attributes:
        link_ref: Original link reference
        matched_page_id: ID of the matched page
        matched_page_title: Title of the matched page
        confidence: Match confidence score (1.0 = exact match)
    """
    link_ref: LinkReference
    matched_page_id: str
    matched_page_title: str
    confidence: float


def print_resolution_report(
    stats: LinkResolutionStats,
    matched_links: list[MatchedLink],
    unmatched_links: list[LinkReference],
    verbose: bool = False,
    dry_run: bool = False
):
    """Print link resolution report to console.
    
    Args:
        stats: Resolution statistics
        matched_links: List of successfully matched links
        unmatched_links: List of unmatched links
        verbose: If True, show detailed match information
        dry_run: If True, indicate this was a dry run
    """
    print("\n" + "=" * 80)
    print("LINK RESOLUTION REPORT")
    if dry_run:
        print("(DRY RUN - No changes made)")
    print("=" * 80)
    
    # Summary statistics
    print(f"\nPages scanned: {stats.total_pages_scanned}")
    print(f"Pages with evernote:// links: {stats.pages_with_links}")
    print(f"\nTotal evernote:// links found: {stats.total_links_found}")
    
    if stats.total_links_found > 0:
        match_rate = (stats.links_matched / stats.total_links_found) * 100
        print(f"  ✓ Matched: {stats.links_matched} ({match_rate:.1f}%)")
        print(f"  ✗ Unmatched: {stats.links_unmatched} ({100 - match_rate:.1f}%)")
        
        if not dry_run and stats.links_updated > 0:
            print(f"\n  → Updated: {stats.links_updated} links")
    
    # Matched links details
    if verbose and matched_links:
        print("\n" + "-" * 80)
        print("MATCHED LINKS")
        print("-" * 80)
        
        for match in matched_links:
            confidence_str = f"{match.confidence:.0%}" if match.confidence < 1.0 else "exact"
            print(f"\n  Link text: '{match.link_ref.link_text}'")
            print(f"  Matched to: '{match.matched_page_title}' ({confidence_str})")
            print(f"  In page: '{match.link_ref.page_title}'")
            print(f"  Original URL: {match.link_ref.original_url}")
    
    # Unmatched links details
    if unmatched_links:
        print("\n" + "-" * 80)
        print("UNMATCHED LINKS")
        print("-" * 80)
        print(f"\nThe following {len(unmatched_links)} link(s) could not be matched to any imported page:")
        
        for link in unmatched_links:
            print(f"\n  Link text: '{link.link_text}'")
            print(f"  In page: '{link.page_title}'")
            print(f"  Original URL: {link.original_url}")
    
    if unmatched_links:
        print("\n" + "=" * 80)
        print("SUGGESTIONS:")
        print("  • Verify the link text matches an imported page title exactly")
        print("  • Try using --match-mode=fuzzy for approximate matching")
        print("  • Check if the referenced pages were actually imported")
        print("=" * 80)
    
    print()


def save_resolution_report(
    stats: LinkResolutionStats,
    matched_links: list[MatchedLink],
    unmatched_links: list[LinkReference],
    output_path: Path,
    dry_run: bool = False
):
    """Save link resolution report to a file.
    
    Args:
        stats: Resolution statistics
        matched_links: List of successfully matched links
        unmatched_links: List of unmatched links
        output_path: Path to save the report
        dry_run: If True, indicate this was a dry run
    """
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("LINK RESOLUTION REPORT\n")
            f.write("=" * 80 + "\n")
            if dry_run:
                f.write("(DRY RUN - No changes made)\n")
                f.write("=" * 80 + "\n")
            
            # Summary statistics
            f.write(f"\nPages scanned: {stats.total_pages_scanned}\n")
            f.write(f"Pages with evernote:// links: {stats.pages_with_links}\n")
            f.write(f"\nTotal evernote:// links found: {stats.total_links_found}\n")
            
            if stats.total_links_found > 0:
                match_rate = (stats.links_matched / stats.total_links_found) * 100
                f.write(f"  ✓ Matched: {stats.links_matched} ({match_rate:.1f}%)\n")
                f.write(f"  ✗ Unmatched: {stats.links_unmatched} ({100 - match_rate:.1f}%)\n")
                
                if not dry_run and stats.links_updated > 0:
                    f.write(f"\n  → Updated: {stats.links_updated} links\n")
            
            # Matched links
            if matched_links:
                f.write("\n" + "-" * 80 + "\n")
                f.write("MATCHED LINKS\n")
                f.write("-" * 80 + "\n")
                
                for match in matched_links:
                    confidence_str = f"{match.confidence:.0%}" if match.confidence < 1.0 else "exact"
                    f.write(f"\nLink text: '{match.link_ref.link_text}'\n")
                    f.write(f"Matched to: '{match.matched_page_title}' ({confidence_str})\n")
                    f.write(f"In page: '{match.link_ref.page_title}'\n")
                    f.write(f"Block type: {match.link_ref.block_type}\n")
                    f.write(f"Original URL: {match.link_ref.original_url}\n")
            
            # Unmatched links
            if unmatched_links:
                f.write("\n" + "-" * 80 + "\n")
                f.write("UNMATCHED LINKS\n")
                f.write("-" * 80 + "\n")
                f.write(f"\nThe following {len(unmatched_links)} link(s) could not be matched:\n")
                
                for link in unmatched_links:
                    f.write(f"\nLink text: '{link.link_text}'\n")
                    f.write(f"In page: '{link.page_title}'\n")
                    f.write(f"Block type: {link.block_type}\n")
                    f.write(f"Original URL: {link.original_url}\n")
            
            if unmatched_links:
                f.write("\n" + "=" * 80 + "\n")
                f.write("SUGGESTIONS:\n")
                f.write("  • Verify the link text matches an imported page title exactly\n")
                f.write("  • Try using --match-mode=fuzzy for approximate matching\n")
                f.write("  • Check if the referenced pages were actually imported\n")
                f.write("=" * 80 + "\n")
        
        logger.info(f"Report saved to {output_path}")
        print(f"\nReport saved to: {output_path}")
        
    except Exception as e:
        logger.error(f"Failed to save report to {output_path}: {e}")
        print(f"ERROR: Could not save report: {e}")
