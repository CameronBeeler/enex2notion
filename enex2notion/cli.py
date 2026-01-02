import logging
import sys
from pathlib import Path

from enex2notion.cli_args import parse_args
from enex2notion.cli_logging import setup_logging
from enex2notion.cli_notion import get_root
from enex2notion.cli_requirements import validate_python_version, validate_requirements, check_optional_tools
from enex2notion.cli_upload import EnexUploader
from enex2notion.cli_wkhtmltopdf import ensure_wkhtmltopdf
from enex2notion.summary_report import ImportSummary, print_report, save_report
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)


def cli(argv):
    args = parse_args(argv)

    rules = Rules.from_args(args)

    setup_logging(args.verbose, args.log)

    # Validation sequence
    validate_python_version()
    validate_requirements()
    check_optional_tools()
    
    # Print configuration summary
    _print_configuration_summary(args, rules)

    if rules.mode_webclips == "PDF":
        ensure_wkhtmltopdf()

    # Validate token and get root page
    wrapper, root_id = get_root(args.token, args.root_page)

    # Determine failed export directory
    failed_export_dir = Path(args.failed_dir) if hasattr(args, "failed_dir") and args.failed_dir else Path.cwd()

    enex_uploader = EnexUploader(
        wrapper=wrapper, root_id=root_id, mode=args.mode, done_file=args.done_file, rules=rules, failed_export_dir=failed_export_dir
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


def _print_configuration_summary(args, rules):
    """Print startup configuration summary."""
    logger.info("="*80)
    logger.info("CONFIGURATION SUMMARY")
    logger.info("="*80)
    
    # ENEX Input Files
    logger.info(f"Input files/directories: {len(args.enex_input)}")
    for idx, enex_path in enumerate(args.enex_input, 1):
        if enex_path.is_file():
            if enex_path.suffix.lower() == '.enex':
                logger.info(f"  [{idx}] ✓ {enex_path} (ENEX file)")
            else:
                logger.warning(f"  [{idx}] ⚠ {enex_path} (Not an ENEX file!)")
        elif enex_path.is_dir():
            enex_count = len(list(enex_path.glob("**/*.enex")))
            logger.info(f"  [{idx}] ✓ {enex_path} (Directory with {enex_count} ENEX files)")
        else:
            logger.error(f"  [{idx}] ✗ {enex_path} (Does not exist!)")
    
    # Upload Configuration
    logger.info(f"Upload mode: {args.mode} {'(Database)' if args.mode == 'DB' else '(Page hierarchy)'}")
    logger.info(f"Root page: '{args.root_page}'")
    logger.info(f"Web clips mode: {rules.mode_webclips}")
    
    # Token Status
    if args.token:
        # Mask token for security
        masked_token = args.token[:10] + "..." + args.token[-4:] if len(args.token) > 14 else "***"
        logger.info(f"Authentication: Integration token ({masked_token})")
    else:
        logger.warning("Authentication: None (DRY RUN - no upload will occur)")
    
    # Options
    options = []
    if rules.add_meta:
        options.append("metadata")
    if rules.add_pdf_preview:
        options.append("PDF preview")
    if rules.condense_lines:
        options.append("condense lines")
    if rules.condense_lines_sparse:
        options.append("condense lines (sparse)")
    if rules.tag:
        options.append(f"tag: {rules.tag}")
    if rules.keep_failed:
        options.append("keep failed")
    if rules.skip_failed:
        options.append("skip failed")
    
    if options:
        logger.info(f"Options: {', '.join(options)}")
    
    logger.info(f"Retry attempts: {args.retry if args.retry > 0 else 'infinite'}")
    
    # Output files
    if args.done_file:
        logger.info(f"Progress tracking: {args.done_file}")
    if args.summary:
        logger.info(f"Summary report: {args.summary}")
    if args.log:
        logger.info(f"Log file: {args.log}")
    if args.failed_dir:
        logger.info(f"Failed notes directory: {args.failed_dir}")
    
    logger.info("="*80)


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
