# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

enex2notion is a Python CLI tool that converts Evernote ENEX export files into Notion pages. It parses ENEX XML files, converts Evernote's proprietary format into Notion blocks, and uploads them via API.

## Modernization Objectives

**CRITICAL**: This codebase requires significant modernization to align with current best practices:

### 1. Replace notion-py with Official Notion API
- **Current**: Uses `notion-vzhd1701-fork` (unofficial API via `token_v2` cookies)
- **Target**: Use official Notion API via `notion-client` (ramnes/notion-sdk-py) with Integration tokens
- **Why**: notion-py uses the unofficial/internal Notion API which is deprecated and prone to breaking. The official Notion API (released 2021, stable since 2022) is the supported approach.
- **Reference**: <cite index="21-1,21-2,24-2,24-3">notion-sdk-py is the simple and easy to use client library for the official Notion API, meant to be a Python version of the reference JavaScript SDK</cite>

### 2. Remove Poetry Dependency
- **Current**: Uses Poetry for dependency management
- **Target**: Use standard `pip` with `requirements.txt` and `pyproject.toml` (build metadata only)
- **Why**: Poetry is not necessary - standard Python tooling suffices for this project

### 3. Simplify Development Tooling
- **Current**: Heavy use of wemake-python-styleguide, flakeheaven, pre-commit with multiple formatters
- **Target**: Minimal essential tooling (black, ruff for linting, pytest for tests)
- **Why**: Remove extraneous complexity that doesn't serve core functionality

### 4. Upgrade to Python 3.12
- **Current**: Python 3.8+ (3.8 EOL October 2024)
- **Target**: Python 3.12+ minimum (supported until October 2028)
- **Why**: <cite index="36-2">Python 3.12 is supported until 2028-10 by Python Core Team</cite> - provides longest support window and modern language features

## Architecture Overview

### Current Processing Pipeline

1. **ENEX Parsing** (`enex_parser.py`, `enex_parser_xml.py`)
   - Parses XML from ENEX files → `EvernoteNote` objects
   - Extracts metadata (title, dates, tags, URL)
   - Decodes base64-encoded resources → `EvernoteResource` objects
   - Detects web clips via note attributes and HTML structure

2. **Note Parsing** (`note_parser/` module)
   - Converts Evernote HTML (ENML) → intermediate Notion block representations
   - Two web clip modes: TXT (text extraction) or PDF (via wkhtmltopdf)
   - Processes formatting, colors, tables, lists, embedded content
   - Post-processing: line condensing, resource reference resolution

3. **Notion Block System** (`notion_blocks/` module)
   - Abstract block representations (text, headers, tables, embeddables)
   - Currently decoupled from notion-py but needs rewrite for official API
   - Block hierarchy: base → containers → uploadable files → text properties

4. **Upload** (`enex_uploader.py`, `enex_uploader_block.py`)
   - Uploads parsed blocks via notion-py library (**NEEDS REPLACEMENT**)
   - Two modes:
     - **DB**: Each notebook → Notion database, notes → rows
     - **PAGE**: Each notebook → page, notes → child pages
   - Tracks uploaded notes via hashes for resume capability
   - Retry mechanism for transient failures

### Key Modules

- **`cli.py` + `cli_*.py`**: Entry point, CLI argument handling
- **`enex_types.py`**: Core data structures (`EvernoteNote`, `EvernoteResource`)
- **`note_parser/elements/`**: HTML element handlers (div, span, table, list)
- **`note_parser/webclip*.py`**: Web clip conversion (TXT/PDF via wkhtmltopdf)
- **`notion_blocks/`**: Notion block type definitions (**NEEDS API ALIGNMENT**)
- **`utils_*.py`**: Color conversion, file validation, random IDs

## Critical Technical Constraints

### Current Authentication (TO BE REPLACED)
- Uses browser `token_v2` cookie from logged-in Notion session
- Requires manual extraction from browser dev tools
- Full account access (security concern)

### Target Authentication
- Use official Notion Integration tokens
- Created via Notion Developer Portal: https://www.notion.com/my-integrations
- Scoped permissions per integration
- Pages/databases must be explicitly shared with integration

### Official API Limitations
When migrating to official API:
- <cite index="20-2,20-3">The official API is still being built out and some functionality like working with views is not currently possible</cite>
- Block type support is limited (verify current API docs for supported blocks)
- API uses different object models than internal API
- Pagination is cursor-based
- Rate limits apply (average 3 requests/second)

### Platform Requirements
- **Python**: 3.12+ (target for modernization)
- **Platform Support**: Ubuntu, MacOS, WSL (per user rules)
- **Optional Dependency**: `wkhtmltopdf` for PDF web clip conversion (external tool)

### File Extension Restrictions
Notion prohibits: `apk`, `app`, `com`, `ear`, `elf`, `exe`, `ipa`, `jar`, `js`, `xap`, `xbe`, `xex`, `xpi`
- Tool automatically appends `.bin` to work around this

## Modernization Action Plan

When implementing changes, follow this sequence:

### Phase 1: API Migration
- Install `notion-client` package (ramnes/notion-sdk-py)
- Study official API docs: https://developers.notion.com/
- Refactor `enex_uploader.py` and `enex_uploader_block.py` to use official API
- Update `notion_blocks/` module to match official API block structures
- Modify CLI to accept Integration token instead of token_v2
- Rewrite tests with new API fixtures (update pytest-vcr cassettes)

### Phase 2: Dependency Simplification
- Remove Poetry: `poetry.lock`, Poetry-specific sections in `pyproject.toml`
- Create `requirements.txt` for runtime dependencies
- Create `requirements-dev.txt` for development dependencies
- Keep minimal `pyproject.toml` for build metadata only
- Update CI/CD workflows (`.github/workflows/test.yml`)
- Remove pre-commit config or simplify to essential hooks only

### Phase 3: Code Quality Simplification
- Remove wemake-python-styleguide, flakeheaven dependencies
- Replace with `ruff` (modern, fast Python linter)
- Keep `black` for formatting
- Keep `mypy` for type checking
- Keep `pytest`, `pytest-cov` for testing
- Update `pyproject.toml` with simplified tool configurations

### Phase 4: Python Version Upgrade
- Update minimum Python version to 3.12 in all config files
- Replace deprecated/old Python patterns with modern equivalents
- Use Python 3.12 features where beneficial (e.g., improved error messages, typing improvements)
- Update CI matrix in `.github/workflows/test.yml` to test 3.12+

### Phase 5: Testing & Documentation
- Verify all existing functionality works with official API
- Document unsupported features (if any) due to API limitations
- Update README with new authentication approach (Integration tokens)
- Update all command examples
- Test cross-platform support (Ubuntu, MacOS, WSL)

## Current State Reference

**DO NOT use these commands after modernization** - they reflect the old Poetry-based setup:

```bash
# OLD (Poetry-based) - TO BE REPLACED
poetry install
poetry run enex2notion
poetry run pytest
poetry run black enex2notion tests
```

**Target commands after modernization**:

```bash
# Setup
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for development

# Run tool
enex2notion  # or python -m enex2notion

# Test
pytest
pytest --cov=enex2notion tests/

# Format & Lint
black enex2notion tests
ruff check enex2notion tests
mypy enex2notion
```

## Testing Notes

- Tests use pytest-vcr with cassettes stored in `tests/cassettes/`
- Cassettes mock Notion API calls for reproducible tests
- Recording new cassettes requires `NOTION_TOKEN` environment variable (Integration token after migration)
- Use fixtures in `conftest.py` for common test data
- Current tests are organized by module:
  - `test_enex_parser.py`: XML parsing and resource extraction
  - `test_note_parser.py`: HTML to Notion block conversion
  - `test_enex_uploader.py`: Upload logic and retry mechanisms
  - `test_cli.py`: CLI argument parsing and integration flows

## Important Development Principles

1. **Preserve all existing functionality** during modernization
2. **Remove extraneous tooling** - if it doesn't solve a specific problem, remove it
3. **Use Notion's official, supported approaches** - avoid unofficial APIs/libraries
4. **Keep it simple** - prefer straightforward Python code over complex abstractions
5. **Support all target platforms** - Ubuntu, MacOS, WSL per user requirements
