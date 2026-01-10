import logging
import sys
from pathlib import Path

from enex2notion.cli_args import parse_args
from enex2notion.cli_logging import setup_logging
from enex2notion.cli_notion import get_root
from enex2notion.cli_requirements import validate_python_version, validate_requirements, check_optional_tools
from enex2notion.cli_check_duplicates import check_duplicates_command
from enex2notion.cli_resolve_links import resolve_links_command
from enex2notion.cli_upload import EnexUploader
from enex2notion.rejected_files_tracker import RejectedFilesTracker
from enex2notion.summary_report import ImportSummary, print_report, save_report
from enex2notion.utils_static import Rules

logger = logging.getLogger(__name__)


def cli(argv):
    args = parse_args(argv)

    setup_logging(args.verbose, args.log if hasattr(args, "log") else None)

    # Validation sequence
    validate_python_version()
    validate_requirements()
    check_optional_tools()
    
    # Route to appropriate command
    if hasattr(args, "command") and args.command == "resolve-links":
        _resolve_links_cli(args)
    elif hasattr(args, "command") and args.command == "check-duplicates":
        _check_duplicates_cli(args)
    else:
        _upload_cli(args)


def _upload_cli(args):
    """Execute the upload command (default)."""
    rules = Rules.from_args(args)
    
    # Print configuration summary
    _print_configuration_summary(args, rules)

    # Validate token and get root page
    wrapper, root_id = get_root(args.token, args.root_page)
    
    # Initialize rejected files tracker
    rejected_tracker = None
    if hasattr(args, "rejected_files") and args.rejected_files:
        rejected_tracker = RejectedFilesTracker(Path(args.rejected_files))
    
    # Get unsupported files directory
    unsupported_dir = getattr(args, "unsupported_files", None)

    enex_uploader = EnexUploader(
        wrapper=wrapper, root_id=root_id, mode=args.mode, done_file=args.done_file, rules=rules, 
        rejected_tracker=rejected_tracker, unsupported_dir=unsupported_dir
    )

    # Track overall import statistics
    summary = ImportSummary()

    # Process all input files/directories
    _process_input(enex_uploader, args.enex_input, summary, 
                   note_title=getattr(args, 'note', None), 
                   note_index=getattr(args, 'note_index', None))

    # Mark import as complete
    summary.complete()

    # Print summary report
    print_report(summary)

    # Save report to file if specified
    if hasattr(args, "summary") and args.summary:
        save_report(summary, Path(args.summary))
    
    # Save rejected files report
    if rejected_tracker:
        rejected_tracker.save_report()


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
    if rules.condense_lines:
        options.append("condense lines")
    if rules.condense_lines_sparse:
        options.append("condense lines (sparse)")
    if rules.tag:
        options.append(f"tag: {rules.tag}")
    
    if options:
        logger.info(f"Options: {', '.join(options)}")
    
    # Output files
    if args.done_file:
        logger.info(f"Progress tracking: {args.done_file}")
    if args.summary:
        logger.info(f"Summary report: {args.summary}")
    if args.log:
        logger.info(f"Log file: {args.log}")
    
    logger.info("="*80)


def _resolve_links_cli(args):
    """Execute the resolve-links command."""
    logger.info("=" * 80)
    logger.info("RESOLVE EVERNOTE LINKS COMMAND")
    logger.info("=" * 80)
    
    # Validate token and get root page
    wrapper, root_id = get_root(args.token, args.root_page)
    
    # Execute link resolution
    resolve_links_command(wrapper, root_id, args)


def _check_duplicates_cli(args):
    """Execute the check-duplicates command."""
    # Validate token and get root page
    wrapper, root_id = get_root(args.token, args.root_page)
    
    # Execute duplicate checking
    check_duplicates_command(wrapper, root_id, args)


def _process_input(enex_uploader: EnexUploader, enex_input: list[Path], summary: ImportSummary, note_title: str | None = None, note_index: int | None = None):
    for path in enex_input:
        if path.is_dir():
            if note_title or note_index:
                logger.warning("Note filtering (--note or --note-index) with directory input will apply to ALL ENEX files in directory")
            logger.info(f"Processing directory '{path.name}'...")
            for enex_file in sorted(path.glob("**/*.enex")):
                notebook_stats = enex_uploader.upload_notebook(enex_file, note_title=note_title, note_index=note_index)
                summary.add_notebook(notebook_stats)
        else:
            notebook_stats = enex_uploader.upload_notebook(path, note_title=note_title, note_index=note_index)
            summary.add_notebook(notebook_stats)


def main():  # pragma: no cover
    try:
        cli(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(1)
