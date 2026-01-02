import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NotebookStats:
    """Statistics for a single notebook import."""

    notebook_name: str
    enex_file: Path
    total: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    failed_directory: Path | None = None

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.successful / self.total) * 100

    @property
    def has_failures(self) -> bool:
        return self.failed > 0


@dataclass
class ImportSummary:
    """Overall statistics for entire import operation."""

    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    notebooks: list[NotebookStats] = field(default_factory=list)

    @property
    def total_notes(self) -> int:
        return sum(nb.total for nb in self.notebooks)

    @property
    def total_successful(self) -> int:
        return sum(nb.successful for nb in self.notebooks)

    @property
    def total_failed(self) -> int:
        return sum(nb.failed for nb in self.notebooks)

    @property
    def total_skipped(self) -> int:
        return sum(nb.skipped for nb in self.notebooks)

    @property
    def success_rate(self) -> float:
        if self.total_notes == 0:
            return 0.0
        return (self.total_successful / self.total_notes) * 100

    @property
    def duration(self) -> str:
        if not self.end_time:
            return "In progress"

        delta = self.end_time - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    @property
    def failed_directories(self) -> list[tuple[str, Path]]:
        """Get list of (notebook_name, unimported_dir) for notebooks with failures/skips."""
        return [(nb.notebook_name, nb.failed_directory) for nb in self.notebooks if nb.has_failures]

    def complete(self):
        """Mark the import as complete."""
        self.end_time = datetime.now()

    def add_notebook(self, stats: NotebookStats):
        """Add notebook statistics to the summary."""
        self.notebooks.append(stats)


def generate_report(summary: ImportSummary) -> str:
    """Generate a formatted text report of import statistics.

    Args:
        summary: ImportSummary containing all statistics

    Returns:
        Formatted report string
    """
    lines = []

    # Header
    lines.append("=" * 60)
    lines.append("ENEX Import Summary")
    if summary.end_time:
        lines.append(f"Completed: {summary.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")

    # Notebook count
    lines.append(f"Notebooks Processed: {len(summary.notebooks)}")
    lines.append("")

    # Per-notebook statistics
    for nb_stats in summary.notebooks:
        lines.append(f"Notebook: {nb_stats.notebook_name} ({nb_stats.enex_file.name})")
        lines.append(f"  Total Notes:     {nb_stats.total:5d}")
        lines.append(f"  Successful:      {nb_stats.successful:5d}  ({nb_stats.success_rate:5.1f}%)")

        if nb_stats.failed > 0:
            fail_pct = (nb_stats.failed / nb_stats.total * 100) if nb_stats.total > 0 else 0
            lines.append(f"  Failed:          {nb_stats.failed:5d}  ({fail_pct:5.1f}%)")

        if nb_stats.skipped > 0:
            skip_pct = (nb_stats.skipped / nb_stats.total * 100) if nb_stats.total > 0 else 0
            lines.append(f"  Skipped:         {nb_stats.skipped:5d}  ({skip_pct:5.1f}%)")

        lines.append("")

    # Overall totals
    lines.append("OVERALL TOTALS:")
    lines.append(f"  Total Notes:     {summary.total_notes:5d}")
    lines.append(f"  Successful:      {summary.total_successful:5d}  ({summary.success_rate:5.1f}%)")

    if summary.total_failed > 0:
        fail_pct = (summary.total_failed / summary.total_notes * 100) if summary.total_notes > 0 else 0
        lines.append(f"  Failed:          {summary.total_failed:5d}  ({fail_pct:5.1f}%)")

    if summary.total_skipped > 0:
        skip_pct = (summary.total_skipped / summary.total_notes * 100) if summary.total_notes > 0 else 0
        lines.append(f"  Skipped:         {summary.total_skipped:5d}  ({skip_pct:5.1f}%)")

    lines.append("")

    # Unimported directories
    if summary.failed_directories:
        lines.append("Unimported Notes Directories:")
        for notebook_name, unimported_dir in summary.failed_directories:
            if unimported_dir:
                lines.append(f"  - {unimported_dir.name}")
        lines.append("")

    # Duration
    lines.append(f"Processing Time: {summary.duration}")
    lines.append("=" * 60)

    return "\n".join(lines)


def print_report(summary: ImportSummary):
    """Print the summary report to console.

    Args:
        summary: ImportSummary containing all statistics
    """
    report = generate_report(summary)
    print("\n" + report + "\n")


def save_report(summary: ImportSummary, report_file: Path):
    """Save the summary report to a file.

    Args:
        summary: ImportSummary containing all statistics
        report_file: Path to save the report
    """
    report = generate_report(summary)

    try:
        report_file.write_text(report + "\n", encoding="utf-8")
        logger.info(f"Summary report saved to: {report_file}")
    except Exception as e:
        logger.error(f"Failed to save summary report: {e}")
        logger.debug("Report save error details", exc_info=e)
