# enex2notion

[![PyPI version](https://img.shields.io/pypi/v/enex2notion?label=version)](https://pypi.python.org/pypi/enex2notion)
[![Python Version](https://img.shields.io/pypi/pyversions/enex2notion.svg)](https://pypi.org/project/enex2notion/)
[![tests](https://github.com/vzhd1701/enex2notion/actions/workflows/test.yml/badge.svg)](https://github.com/vzhd1701/enex2notion/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/vzhd1701/enex2notion/branch/master/graph/badge.svg)](https://codecov.io/gh/vzhd1701/enex2notion)

Easy way to import [Evernote's](https://www.evernote.com/) `*.enex` files to [Notion.so](https://notion.so)


## Quick Start

```bash
# Clone and install
git clone https://github.com/CameronBeeler/enex2notion.git
cd enex2notion/
pip install -r requirements.txt

# Set up Notion Integration token (see below)
export NOTION_TOKEN="secret_your_integration_token_here"

# Test (dry run - no upload)
python -m enex2notion your_notebook.enex --verbose

# Upload to Notion
python -m enex2notion your_notebook.enex --use-env --summary summary.txt
```

### What is preserved

- Embedded files and images are uploaded to Notion
  - nested images will appear after paragraph
- Text formatting (**bold**, _italic_, etc) and colors
- Tables are converted to the new format (no colspans though)
- Tasks
- Web Clips
  - as plain text or PDFs, see [below](#web-clips)
- Everything else basically

### What is lost

- Paragraph alignment
- Subscript and superscript formatting
- Custom fonts and font sizes
- Encrypted blocks
  - just decrypt them before export

## Installation

If you are not familiar with command line programs, take a look at these step-by-step guides:

### [Step-by-step guide for Windows](https://vzhd1701.notion.site/How-to-use-enex2notion-on-Windows-6fa980b489ab4414a5317e631e7f6bc6)

### [Step-by-step guide for macOS](https://vzhd1701.notion.site/How-to-use-enex2notion-on-macOS-a912dd63e3d14da886a413d3f83efb67)

### Using portable binary

[**Download the latest binary release**](https://github.com/vzhd1701/enex2notion/releases/latest) for your OS.

### With [Homebrew](https://brew.sh/) (Recommended for macOS)

```bash
$ brew install enex2notion
```

### With [**PIPX**](https://github.com/pipxproject/pipx) (Recommended for Linux & Windows)

```shell
$ pipx install enex2notion
```

### With [**Docker**](https://docs.docker.com/)

[![Docker Image Size (amd64)](<https://img.shields.io/docker/image-size/vzhd1701/enex2notion?arch=amd64&label=image%20size%20(amd64)>)](https://hub.docker.com/r/vzhd1701/enex2notion)
[![Docker Image Size (arm64)](<https://img.shields.io/docker/image-size/vzhd1701/enex2notion?arch=arm64&label=image%20size%20(arm64)>)](https://hub.docker.com/r/vzhd1701/enex2notion)

This command maps current directory `$PWD` to the `/input` directory in the container. You can replace `$PWD` with a directory that contains your `*.enex` files. When running commands like `enex2notion /input` refer to your local mapped directory as `/input`.

```shell
$ docker run --rm -t -v "$PWD":/input vzhd1701/enex2notion:latest
```

### With PIP

```bash
$ pip install --user enex2notion
```

**Python 3.12 or later required.**

### From source

This project uses standard Python packaging tools.

```shell
$ git clone https://github.com/CameronBeeler/enex2notion.git
$ cd enex2notion/
$ pip install -r requirements.txt
$ python -m enex2notion
```

**Or install in development mode:**

```shell
$ pip install -e .
$ enex2notion
```

## Usage

```shell
$ enex2notion --help
usage: enex2notion [-h] [--token TOKEN] [OPTION ...] FILE/DIR [FILE/DIR ...]

Uploads ENEX files to Notion

positional arguments:
  FILE/DIR                   ENEX files or directories to upload

options:
  -h, --help                 show this help message and exit
  --token TOKEN              Notion Integration token (create at https://www.notion.com/my-integrations). Can also use --use-env to read from NOTION_TOKEN environment variable.
                             Example: export NOTION_TOKEN="secret_your_token_here" [NEEDED FOR UPLOAD]
  --use-env                  use NOTION_TOKEN environment variable for authentication instead of --token argument
  --root-page NAME           root page name for the imported notebooks, it will be created if it does not exist (default: "Evernote ENEX Import")
  --mode {DB,PAGE}           upload each ENEX as database (DB) or page with children (PAGE) (default: DB)
  --add-meta                 include metadata (created, tags, etc) in notes, makes sense only with PAGE mode
  --tag TAG                  add custom tag to uploaded notes
  --condense-lines           condense text lines together into paragraphs to avoid making block per line
  --condense-lines-sparse    like --condense-lines but leaves gaps between paragraphs
  --done-file FILE           file for uploaded notes hashes to resume interrupted upload
  --summary FILE             save import summary report to file (always printed to console)
  --rejected-files FILE      save rejected/unsupported files report to CSV file
  --log FILE                 file to store program log
  --verbose                  output debug information
  --version                  show program's version number and exit
```

### Input

You can pass single `*.enex` files or directories. The program will recursively scan directories for `*.enex` files.

### Integration Token & Authentication

The upload requires a Notion Integration token. To set one up:

1. Go to https://www.notion.com/my-integrations
2. Click "+ New integration"
3. Give it a name (e.g., "enex2notion") and submit
4. Copy the Integration token (starts with `secret_`)
5. Create a page in Notion to import into
6. Share that page with your Integration (via "..." menu → "Add connections")

**Using Environment Variable (Recommended)**:

```bash
# For current session
export NOTION_TOKEN="secret_your_integration_token_here"

# To persist across sessions, add to shell profile:
# For bash: add to ~/.bashrc or ~/.bash_profile
# For zsh: add to ~/.zshrc
echo 'export NOTION_TOKEN="secret_your_integration_token_here"' >> ~/.zshrc
source ~/.zshrc

# Use with --use-env flag
python -m enex2notion notebook.enex --use-env
```

**Or pass token directly**:

```bash
python -m enex2notion notebook.enex --token secret_your_token_here
```

**Dry Run Mode**: The program can run without authentication. It will not make any network requests without a token. Executing a dry run with `--verbose` is an excellent way to check if your `*.enex` files are parsed correctly before uploading.

### Upload continuation

The upload will take some time since each note is uploaded block-by-block, so you'll probably need some way of resuming it. `--done-file` is precisely for that. All uploaded note hashes will be stored there, so the next time you start, the upload will continue from where you left off.

All uploaded notebooks will appear under the automatically created `Evernote ENEX Import` page. You can change that name with the `--root-page` option. The program will mark unfinished notes with `[UNFINISHED UPLOAD]` text in the title. After successful upload, the mark will be removed.

### Summary Reports & Partial Imports

After import completion, a detailed summary report is displayed showing:
- Total notes per notebook and overall
- Success/failure/skip rates with percentages
- Processing time

#### Partial Import System

**Every Evernote note creates a Notion page**, even if import errors occur. Notes with import errors are marked as "partial imports" with:

1. **Inline error callout** at the top of the page listing what failed
2. **Source URL bookmark** (for web clips) so you can access the original
3. **"Partial Import" checkbox** set to `true` in database mode
4. **Exception tracking** - An "Exceptions" page is created under your root page with links to all partially imported notes organized by notebook

**Setting up Exception View (DB mode only)**:

To easily filter partial imports in your database:

1. Open the database in Notion
2. Click "+ New view" → "Table"
3. Name it "Exceptions"
4. Add filter: "Partial Import" → "is" → "Checked"
5. (Optional) On default Table view: hover over Name column footer → "Calculate" → "Count All"

**Note**: Database views must be created manually in Notion - the API does not support programmatic view creation.

Use `--summary FILE` to save the import report to a file.

### Upload modes

The `--mode` option allows you to choose how to upload your notebooks: as databases or pages. `DB` mode is the default since Notion itself uses this mode when importing from Evernote. `PAGE` mode makes the tree feel like the original Evernote notebooks hierarchy.

Since `PAGE` mode does not benefit from having separate space for metadata, you can still preserve the note's original meta with the `--add-meta` option. It will attach a callout block with all meta info as a first block in each note [like this](https://imgur.com/a/lJTbprH).

### Web Clips

Due to Notion's limitations Evernote web clips cannot be uploaded as-is. `enex2notion` provides two modes with the `--mode-webclips` option:

- `TXT`, converting them to text, stripping all HTML formatting \[Default\]

  - similar to Evernote's "Simplify & Make Editable"

- `PDF`, converting them to PDF, keeping HTML formatting as close as possible

  - web clips are converted using [wkhtmltopdf](https://wkhtmltopdf.org/), see [this page](https://github.com/JazzCore/python-pdfkit/wiki/Installing-wkhtmltopdf) on how to install it

Since Notion's gallery view does not provide thumbnails for embedded PDFs, you have the `--add-pdf-preview` option to extract the first page of generated PDF as a preview for the web clip page.

### Banned file extensions

Notion prohibits uploading files with certain extensions. The list consists of extensions for executable binaries, supposedly to prevent spreading malware. `enex2notion` will automatically add a `bin` extension to those files to circumvent this limitation. List of banned extensions: `apk`, `app`, `com`, `ear`, `elf`, `exe`, `ipa`, `jar`, `js`, `xap`, `xbe`, `xex`, `xpi`.

### Misc

The `--condense-lines` option is helpful if you want to save up some space and make notes look more compact. [Example](https://imgur.com/a/sV0X8z7).

The `--condense-lines-sparse` does the same thing as `--condense-lines`, but leaves gaps between paragraphs. [Example](https://imgur.com/a/OBzeqn7).

The `--tag` option allows you to add a custom tag to all uploaded notes. It will add this tag to existing tags if the note already has any.

## Resolving Evernote Links

After importing your notes, you may have internal Evernote links (`evernote://` URLs) that are broken. The `resolve-links` command finds these links and replaces them with working Notion page links.

### What Links Are Resolved

The tool handles two formats of evernote:// links:

1. **Markdown format**: `[text](evernote://...)` - created by enex2notion
2. **Rich text links**: Text with `evernote://` URL - created by Notion's official importer

### Basic Usage

```shell
# Resolve all links (requires authentication)
$ enex2notion --resolve-links --use-env

# Or with token directly
$ enex2notion --resolve-links --token secret_your_token_here
```

### Command Options

```
--token TOKEN          Notion Integration token [REQUIRED]
--use-env              Use NOTION_TOKEN environment variable
--root-page NAME       Root page to scan (default: "Evernote ENEX Import")
--page NAME            Analyze only a specific page by name
--page-list FILE       Path to page list cache file (JSON)
--match-mode MODE      Matching strategy: exact, case-insensitive, fuzzy
                       (default: case-insensitive)
--dry-run              Show matches without updating links
--summary FILE         Save resolution report to file
--verbose              Show detailed match information
--log FILE             Save program log to file
```

### Page List Caching

For large imports, collecting all page names can take time. Use `--page-list` to cache the page map:

```shell
# First run: scan Notion and save page list
$ enex2notion --resolve-links --use-env --page-list pages.json

# Subsequent runs: load from cache (much faster)
$ enex2notion --resolve-links --use-env --page-list pages.json
```

### Analyzing a Single Page

To test or fix links in one page:

```shell
# Analyze a specific page
$ enex2notion --resolve-links --use-env --page "My Note Title" --dry-run

# Update links in that page
$ enex2notion --resolve-links --use-env --page "My Note Title"
```

### Matching Strategies

- **exact**: Case-sensitive exact match (most strict)
- **case-insensitive**: Ignores case differences (default, recommended)
- **fuzzy**: Uses similarity matching for close matches (most lenient)

```shell
# Use fuzzy matching for approximate matches
$ enex2notion --resolve-links --use-env --match-mode fuzzy
```

### Example Workflow

```shell
# 1. Import your notes
$ enex2notion --use-env my_notebooks/

# 2. Scan and cache all page names
$ enex2notion --resolve-links --use-env --page-list pages.json --dry-run

# 3. Review the report, then update links
$ enex2notion --resolve-links --use-env --page-list pages.json --summary report.txt

# 4. Fix unmatched links for a specific page
$ enex2notion --resolve-links --use-env --page "Problematic Note" --match-mode fuzzy
```

### Understanding the Report

The resolution report shows:
- Total pages scanned and pages with evernote:// links
- Matched links (successfully resolved)
- Unmatched links (link text doesn't match any page)
- For unmatched links: suggestions for manual resolution

## Examples

### Checking notes before upload

```shell
$ enex2notion --verbose my_notebooks/
```

### Uploading notes from a single notebook

```shell
$ enex2notion --token <YOUR_TOKEN_HERE> "notebook.enex"
```

### Uploading with the option to continue later

```shell
$ enex2notion --token <YOUR_TOKEN_HERE> --done-file done.txt "notebook.enex"
```

## Getting help

If you found a bug or have a feature request, please [open a new issue](https://github.com/vzhd1701/enex2notion/issues/new/choose).

If you have a question about the program or have difficulty using it, you are welcome to [the discussions page](https://github.com/vzhd1701/enex2notion/discussions). You can also mail me directly, I'm always happy to help.
