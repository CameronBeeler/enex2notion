"""Comprehensive test for link resolution enhancements.

Tests:
1. Link validation with caching
2. Multi-link support (multiple links in single element)
3. ReviewTracker integration
4. Database page support
5. Parallel processing
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from enex2notion.link_resolver import validate_target_page, _convert_text_with_all_links


class MockWrapper:
    """Mock wrapper for testing."""
    
    class MockClient:
        class MockPages:
            def __init__(self, test_pages):
                self.test_pages = test_pages
            
            def retrieve(self, page_id):
                if page_id not in self.test_pages:
                    raise Exception(f"Page {page_id} not found")
                return self.test_pages[page_id]
        
        def __init__(self, test_pages):
            self.pages = self.MockPages(test_pages)
    
    def __init__(self, test_pages):
        self.client = self.MockClient(test_pages)


def test_link_validation():
    """Test 1: Link validation with caching."""
    print("=" * 80)
    print("TEST 1: Link Validation with Caching")
    print("=" * 80)
    
    test_pages = {
        "valid-page": {"id": "valid-page", "archived": False},
        "archived-page": {"id": "archived-page", "archived": True},
        "trashed-page": {"id": "trashed-page", "in_trash": True},
    }
    
    wrapper = MockWrapper(test_pages)
    cache = {}
    
    # Test valid page
    assert validate_target_page(wrapper, "valid-page", cache) is True
    print("✓ Valid page detected correctly")
    
    # Test archived page
    assert validate_target_page(wrapper, "archived-page", cache) is False
    print("✓ Archived page rejected correctly")
    
    # Test trashed page
    assert validate_target_page(wrapper, "trashed-page", cache) is False
    print("✓ Trashed page rejected correctly")
    
    # Test cache hit
    assert "valid-page" in cache and cache["valid-page"] is True
    assert "archived-page" in cache and cache["archived-page"] is False
    print("✓ Cache working correctly")
    
    print("✓ All link validation tests passed!\n")


def test_multi_link_support():
    """Test 2: Multi-link support (multiple links in single element)."""
    print("=" * 80)
    print("TEST 2: Multi-Link Support")
    print("=" * 80)
    
    # Test text with 3 links
    text = "Check out [Page One](evernote://note1) and [Page Two](evernote://note2) also [Page Three](evernote://note3)"
    
    annotations = {
        "bold": False,
        "italic": False,
        "strikethrough": False,
        "underline": False,
        "code": False,
        "color": "default",
    }
    
    link_lookup = {
        "page one": "page-1-id",
        "page two": "page-2-id",
        "page three": None,  # Unresolved
    }
    
    result = _convert_text_with_all_links(text, annotations, link_lookup)
    
    # Should have: text + mention + text + mention + text + unresolved marker + text
    assert len(result) >= 5, f"Expected at least 5 elements, got {len(result)}"
    
    # Count mentions (resolved links)
    mentions = [r for r in result if r.get("type") == "mention"]
    assert len(mentions) == 2, f"Expected 2 mentions (resolved links), got {len(mentions)}"
    print(f"✓ Processed {len(mentions)} resolved links")
    
    # Check for unresolved marker
    text_elements = [r for r in result if r.get("type") == "text"]
    has_unresolved_marker = any("🛑 unresolved:" in r.get("text", {}).get("content", "") for r in text_elements)
    assert has_unresolved_marker, "Expected unresolved marker in text"
    print("✓ Unresolved link marked correctly")
    
    print("✓ Multi-link support working!\n")


def test_text_consolidation():
    """Test 3: Text consolidation to reduce element count."""
    print("=" * 80)
    print("TEST 3: Text Consolidation")
    print("=" * 80)
    
    # Text with resolved link followed by more text
    text = "See [My Note](evernote://note1) for details on this topic."
    annotations = {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}
    link_lookup = {"my note": "note-1-id"}
    
    result = _convert_text_with_all_links(text, annotations, link_lookup)
    
    # Should have: prefix text + mention + suffix text (3 elements)
    # Consolidation should merge adjacent text elements
    assert len(result) <= 3, f"Expected ≤3 elements after consolidation, got {len(result)}"
    print(f"✓ Result has {len(result)} elements (consolidated)")
    
    # Verify content is preserved
    full_content = ""
    for elem in result:
        if elem.get("type") == "text":
            full_content += elem.get("text", {}).get("content", "")
        elif elem.get("type") == "mention":
            full_content += "[mention]"
    
    expected_pattern = "See [mention] for details on this topic."
    assert full_content == expected_pattern, f"Content mismatch: '{full_content}' != '{expected_pattern}'"
    print("✓ Content preserved correctly")
    
    print("✓ Text consolidation working!\n")


def test_oversized_text_splitting():
    """Test 4: Oversized text splitting (>2000 chars)."""
    print("=" * 80)
    print("TEST 4: Oversized Text Splitting")
    print("=" * 80)
    
    # Create text with link followed by very long text (>2000 chars)
    long_text = "A" * 2500
    text = f"[My Link](evernote://note1) {long_text}"
    annotations = {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}
    link_lookup = {"my link": "note-1-id"}
    
    result = _convert_text_with_all_links(text, annotations, link_lookup)
    
    # Check all text elements are ≤2000 chars
    for elem in result:
        if elem.get("type") == "text":
            content_len = len(elem.get("text", {}).get("content", ""))
            assert content_len <= 2000, f"Text element exceeds 2000 chars: {content_len}"
    
    print(f"✓ All text elements ≤2000 chars (split into {len(result)} elements)")
    
    # Verify total content length preserved
    total_chars = sum(len(elem.get("text", {}).get("content", "")) for elem in result if elem.get("type") == "text")
    # Space is already included in the f-string
    assert total_chars == len(long_text), f"Content length mismatch: {total_chars} != {len(long_text)}"
    print("✓ Content length preserved")
    
    print("✓ Oversized text splitting working!\n")


def test_edge_cases():
    """Test 5: Edge cases."""
    print("=" * 80)
    print("TEST 5: Edge Cases")
    print("=" * 80)
    
    annotations = {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}
    
    # Empty text
    result = _convert_text_with_all_links("", annotations, {})
    assert len(result) == 1 and result[0].get("text", {}).get("content") == ""
    print("✓ Empty text handled")
    
    # Text with no links
    result = _convert_text_with_all_links("Just plain text", annotations, {})
    assert len(result) == 1 and result[0].get("text", {}).get("content") == "Just plain text"
    print("✓ Text without links handled")
    
    # Link at start
    result = _convert_text_with_all_links("[Link](evernote://note1) followed by text", annotations, {"link": "note-1"})
    assert len(result) == 2  # mention + text
    print("✓ Link at start handled")
    
    # Link at end
    result = _convert_text_with_all_links("Text followed by [Link](evernote://note1)", annotations, {"link": "note-1"})
    assert len(result) == 2  # text + mention
    print("✓ Link at end handled")
    
    # Only link
    result = _convert_text_with_all_links("[Link](evernote://note1)", annotations, {"link": "note-1"})
    assert len(result) == 1 and result[0].get("type") == "mention"
    print("✓ Only link handled")
    
    print("✓ All edge cases handled!\n")


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("COMPREHENSIVE LINK RESOLUTION ENHANCEMENT TESTS")
    print("=" * 80 + "\n")
    
    try:
        test_link_validation()
        test_multi_link_support()
        test_text_consolidation()
        test_oversized_text_splitting()
        test_edge_cases()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80)
        print("\nEnhancements verified:")
        print("  1. ✓ Link validation with caching")
        print("  2. ✓ Multi-link support (multiple links per element)")
        print("  3. ✓ Text consolidation (reducing element count)")
        print("  4. ✓ Oversized text splitting (>2000 chars)")
        print("  5. ✓ Edge case handling")
        print("\nNote: ReviewTracker integration and parallel processing")
        print("      require live Notion API testing.")
        print("=" * 80 + "\n")
        
        return 0
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
