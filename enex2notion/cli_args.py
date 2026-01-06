import argparse
import os
from pathlib import Path

from enex2notion.version import __version__

HELP_ARGS_WIDTH = 29


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="enex2notion",
        description="Uploads ENEX files to Notion",
        usage="%(prog)s [-h] [--token TOKEN] [OPTION ...] FILE/DIR [FILE/DIR ...]",
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog, max_help_position=HELP_ARGS_WIDTH
        ),
    )

    schema = {
        "enex_input": {
            "type": Path,
            "nargs": "*",  # Make optional for --resolve-links
            "help": "ENEX files or directories to upload",
            "metavar": "FILE/DIR",
        },
        "--resolve-links": {
            "action": "store_true",
            "help": "resolve evernote:// links in imported Notion pages",
        },
        "--page": {
            "help": (
                "(resolve-links only) analyze only a specific page by name"
                " (useful for testing or fixing individual pages)"
            ),
            "metavar": "NAME",
        },
        "--page-list": {
            "type": Path,
            "help": (
                "(resolve-links only) path to page list cache file (JSON format)."
                " If file exists, load page map from it instead of scanning Notion."
                " If file doesn't exist, scan Notion and save the page map to this file."
            ),
            "metavar": "FILE",
        },
        "--match-mode": {
            "choices": ["exact", "case-insensitive", "fuzzy"],
            "default": "case-insensitive",
            "help": (
                "(resolve-links only) matching strategy: exact (case-sensitive),"
                " case-insensitive, or fuzzy (similarity-based)"
                " (default: case-insensitive)"
            ),
        },
        "--dry-run": {
            "action": "store_true",
            "help": "(resolve-links only) show matches without updating links",
        },
        "--queue": {
            "type": Path,
            "metavar": "FILE",
            "help": "(resolve-links only) path to queue file (TSV: page_id\tpage_title). If missing, it will be created from the collected pages.",
        },
        "--limit": {
            "type": int,
            "metavar": "N",
            "help": "(resolve-links only) process at most N pages from the queue in this run",
        },
        "--token": {
            "help": (
                "Notion Integration token (create at https://www.notion.com/my-integrations)."
                " Can also use --use-env to read from NOTION_TOKEN environment variable."
                " Example: export NOTION_TOKEN=\"secret_your_token_here\""
                " [NEEDED FOR UPLOAD]"
            ),
        },
        "--use-env": {
            "action": "store_true",
            "help": (
                "use NOTION_TOKEN environment variable for authentication"
                " instead of --token argument"
            ),
        },
        "--root-page": {
            "default": "Evernote ENEX Import",
            "help": (
                "root page name for the imported notebooks,"
                " it will be created if it does not exist"
                ' (default: "Evernote ENEX Import")'
            ),
            "metavar": "NAME",
        },
        "--mode": {
            "choices": ["DB", "PAGE"],
            "default": "DB",
            "help": (
                "upload each ENEX as database (DB) or page with children (PAGE)"
                " (default: DB)"
            ),
        },
        "--add-meta": {
            "action": "store_true",
            "help": (
                "include metadata (created, tags, etc) in notes,"
                " makes sense only with PAGE mode"
            ),
        },
        "--tag": {
            "help": "add custom tag to uploaded notes",
        },
        "--condense-lines": {
            "action": "store_true",
            "help": (
                "condense text lines together into paragraphs"
                " to avoid making block per line"
            ),
        },
        "--condense-lines-sparse": {
            "action": "store_true",
            "help": "like --condense-lines but leaves gaps between paragraphs",
        },
        "--done-file": {
            "type": Path,
            "metavar": "FILE",
            "help": "file for uploaded notes hashes to resume interrupted upload",
        },
        "--summary": {
            "type": Path,
            "metavar": "FILE",
            "help": "save import summary report to file (always printed to console)",
        },
        "--rejected-files": {
            "type": Path,
            "metavar": "FILE",
            "help": "save rejected/unsupported files report to CSV file",
        },
        "--log": {
            "type": Path,
            "metavar": "FILE",
            "help": "file to store program log",
        },
        "--verbose": {
            "action": "store_true",
            "help": "output debug information",
        },
        "--version": {
            "action": "version",
            "version": f"%(prog)s {__version__}",  # noqa: WPS323
        },
    }

    for arg, arg_params in schema.items():
        parser.add_argument(arg, **arg_params)

    args = parser.parse_args(argv)

    # Handle environment variable token
    if args.use_env:
        env_token = os.environ.get("NOTION_TOKEN")
        if not env_token:
            parser.error(
                "--use-env specified but NOTION_TOKEN environment variable is not set.\n"
                "Set it with: export NOTION_TOKEN=\"secret_your_token_here\""
            )
        args.token = env_token
    elif not args.token and "NOTION_TOKEN" in os.environ:
        # Auto-detect if not explicitly specified
        args.token = os.environ["NOTION_TOKEN"]
    
    # Validate arguments based on mode
    if args.resolve_links:
        # Resolve-links mode: token is required, enex_input is not
        if not args.token:
            parser.error(
                "--token or --use-env is required for --resolve-links.\n"
                "Create an Integration token at https://www.notion.com/my-integrations"
            )
        args.command = "resolve-links"
        # Set enex_input to None in resolve-links mode (not used)
        args.enex_input = None
    else:
        # Upload mode: enex_input is required
        if not args.enex_input:
            parser.error("the following arguments are required: FILE/DIR")
        args.command = "upload"
    
    return args
