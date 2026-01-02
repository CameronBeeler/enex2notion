import logging
import sys
from pathlib import Path

from enex2notion.cli_args import parse_args
from enex2notion.cli_logging import setup_logging
from enex2notion.cli_notion import get_root
from enex2notion.cli_upload import EnexUploader
from enex2notion.cli_wkhtmltopdf import ensure_wkhtmltopdf
from enex2notion.summary_report import ImportSummary, print_report, save_report
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)


def cli(argv):
    args = parse_args(argv)

    rules = Rules.from_args(args)

    setup_logging(args.verbose, args.log)

    if rules.mode_webclips == "PDF":
        ensure_wkhtmltopdf()

    root = get_root(args.token, args.root_page)

    # Determine failed export directory
    failed_export_dir = Path(args.failed_dir) if hasattr(args, "failed_dir") and args.failed_dir else Path.cwd()

    enex_uploader = EnexUploader(
        import_root=root, mode=args.mode, done_file=args.done_file, rules=rules, failed_export_dir=failed_export_dir
    )

    # Track overall import statistics
    summary = ImportSummary()

    # Process all input files/directories
    _process_input(enex_uploader, args.enex_input, summary)

    # Mark import as complete
    summary.complete()

    # Print summary report
    print_report(summary)

    # Save report to file if specified
    if hasattr(args, "summary") and args.summary:
        save_report(summary, Path(args.summary))


def _process_input(enex_uploader: EnexUploader, enex_input: list[Path], summary: ImportSummary):
    for path in enex_input:
        if path.is_dir():
            logger.info(f"Processing directory '{path.name}'...")
            for enex_file in sorted(path.glob("**/*.enex")):
                notebook_stats = enex_uploader.upload_notebook(enex_file)
                summary.add_notebook(notebook_stats)
        else:
            notebook_stats = enex_uploader.upload_notebook(path)
            summary.add_notebook(notebook_stats)


def main():  # pragma: no cover
    try:
        cli(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(1)
