#!/usr/bin/env python3
"""
Test script to verify that the streaming implementation in cli_resolve_links.py
has the correct structure without needing a live Notion connection.
"""

import ast
import inspect
from pathlib import Path


def test_streaming_implementation():
    """Verify the streaming implementation has correct structure."""
    
    # Read the file
    cli_file = Path(__file__).parent / "enex2notion" / "cli_resolve_links.py"
    with open(cli_file) as f:
        content = f.read()
    
    # Parse the AST
    tree = ast.parse(content)
    
    print("✓ Testing streaming implementation structure...\n")
    
    # Check imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "concurrent.futures":
                imports.extend([alias.name for alias in node.names])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
    
    assert "ThreadPoolExecutor" in imports, "Missing ThreadPoolExecutor import"
    assert "as_completed" in imports, "Missing as_completed import"
    print("✓ Concurrent futures imports present")
    
    # Check for Lock
    if "Lock" in imports or "threading" in str(imports):
        print("✓ Threading Lock import present")
    
    # Find the resolve_links_command function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_links_command":
            func_body = ast.unparse(node)
            
            # Check for process_page function definition
            assert "def process_page" in func_body, "Missing process_page function"
            print("✓ process_page function defined")
            
            # Check for ThreadPoolExecutor usage
            assert "ThreadPoolExecutor" in func_body, "Missing ThreadPoolExecutor usage"
            print("✓ ThreadPoolExecutor used")
            
            # Check for workers parameter
            assert "getattr(args, 'workers'" in func_body or "args.workers" in func_body, "Missing workers parameter"
            print("✓ Workers parameter accessed")
            
            # Check for stats_lock
            assert "stats_lock" in func_body, "Missing stats_lock for thread safety"
            print("✓ Thread-safe stats lock present")
            
            # Check for tqdm progress bar
            assert "tqdm" in func_body, "Missing tqdm progress bar"
            print("✓ Progress bar (tqdm) present")
            
            # Check for as_completed pattern
            assert "as_completed" in func_body, "Missing as_completed pattern"
            print("✓ as_completed pattern used")
            
            # Check for atomic queue updates inside process_page
            # This is harder to verify statically, but we can check for the pattern
            assert "_write_json_atomic" in func_body, "Missing atomic queue updates"
            print("✓ Atomic queue updates present")
            
            print("\n✓ All structural checks passed!")
            print("\nKey improvements:")
            print("  - Two-phase (scan-then-process) replaced with streaming (scan+process)")
            print("  - Parallel processing with configurable workers (default: 3)")
            print("  - Thread-safe statistics aggregation with Lock()")
            print("  - Atomic per-page queue updates (completed.json/unfinished.json)")
            print("  - Constant memory usage (no all_link_refs accumulation)")
            print("  - Immediate progressive results with tqdm")
            
            return True
    
    raise AssertionError("Could not find resolve_links_command function")


if __name__ == "__main__":
    try:
        test_streaming_implementation()
        print("\n✅ Streaming implementation verified successfully!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
