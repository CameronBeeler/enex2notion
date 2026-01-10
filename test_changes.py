#!/usr/bin/env python3
"""Quick test for Change 1 and Change 2."""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from enex2notion.notion_block_converter import _convert_text_prop_with_overflow, _create_failed_upload_placeholder
from enex2notion.parse_warnings import init_warnings, get_warnings, clear_warnings


def test_change_1():
    """Test that paragraph splits don't create warnings."""
    print("=" * 60)
    print("TEST CHANGE 1: Paragraph splits shouldn't create warnings")
    print("=" * 60)
    
    # Create a mock text property with > 100 formatting segments
    class MockTextProp:
        def __init__(self):
            # Create 150 simple text segments
            self.properties = [[f"segment{i}"] for i in range(150)]
    
    # Initialize warnings
    clear_warnings()
    init_warnings()
    
    # Convert with overflow
    text_prop = MockTextProp()
    main_text, overflow = _convert_text_prop_with_overflow(text_prop)
    
    # Check warnings
    warnings = get_warnings()
    
    print(f"\nCreated text with 150 segments")
    print(f"Main block: {len(main_text)} items")
    print(f"Overflow blocks: {len(overflow)} blocks")
    print(f"Warnings generated: {len(warnings)}")
    
    if warnings:
        print("\n⚠️  FAILED: Warnings were generated:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("\n✅ PASSED: No warnings generated (as expected)")
    
    return len(warnings) == 0


def test_change_2():
    """Test that placeholder includes download location."""
    print("\n" + "=" * 60)
    print("TEST CHANGE 2: Placeholder should include download location")
    print("=" * 60)
    
    # Test 1: Placeholder without download location
    placeholder1 = _create_failed_upload_placeholder("test.txt", "file", None)
    
    print("\n1. Placeholder WITHOUT download location:")
    print(f"   Icon: {placeholder1['callout']['icon']['emoji']}")
    print(f"   Color: {placeholder1['callout']['color']}")
    message1 = placeholder1['callout']['rich_text'][0]['text']['content']
    print(f"   Message: {message1[:80]}...")
    
    # Test 2: Placeholder with download location
    download_path = "/Users/cam/unsupported/Notebook/Note/document.key"
    placeholder2 = _create_failed_upload_placeholder("document.key", "file", download_path)
    
    print("\n2. Placeholder WITH download location:")
    print(f"   Icon: {placeholder2['callout']['icon']['emoji']}")
    print(f"   Color: {placeholder2['callout']['color']}")
    message2 = placeholder2['callout']['rich_text'][0]['text']['content']
    print(f"   Message: {message2[:100]}...")
    
    # Verify expectations
    has_warning_icon = placeholder1['callout']['icon']['emoji'] == '⚠️'
    has_yellow_bg = placeholder1['callout']['color'] == 'yellow_background'
    has_clip_icon = placeholder2['callout']['icon']['emoji'] == '📎'
    has_blue_bg = placeholder2['callout']['color'] == 'blue_background'
    has_path_in_msg = download_path in message2
    
    print("\n   Checks:")
    print(f"   {'✅' if has_warning_icon else '❌'} Without location: warning icon")
    print(f"   {'✅' if has_yellow_bg else '❌'} Without location: yellow background")
    print(f"   {'✅' if has_clip_icon else '❌'} With location: clip icon")
    print(f"   {'✅' if has_blue_bg else '❌'} With location: blue background")
    print(f"   {'✅' if has_path_in_msg else '❌'} With location: path in message")
    
    all_passed = all([has_warning_icon, has_yellow_bg, has_clip_icon, has_blue_bg, has_path_in_msg])
    
    if all_passed:
        print("\n✅ PASSED: All checks passed")
    else:
        print("\n⚠️  FAILED: Some checks failed")
    
    return all_passed


if __name__ == "__main__":
    print("\nRunning quick tests for Changes 1 and 2...")
    print()
    
    test1_passed = test_change_1()
    test2_passed = test_change_2()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Change 1 (no warnings for splits): {'✅ PASSED' if test1_passed else '❌ FAILED'}")
    print(f"Change 2 (download location in placeholder): {'✅ PASSED' if test2_passed else '❌ FAILED'}")
    print()
    
    if test1_passed and test2_passed:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("⚠️  Some tests failed")
        sys.exit(1)
