"""Link resolver for converting evernote:// references to Notion inline page links.

Pass 1: Markdown format [note-name](evernote://url)
Pass 2: Existing inline links with evernote:// in href attribute
"""
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Notion's character limit per rich_text element
MAX_RICH_TEXT_LENGTH = 2000

# Block types that can contain rich_text content
RICH_TEXT_BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do",
    "quote", "callout", "toggle",
}

# Safety thresholds for pre-normalization
SAFE_CHAR_PER_BLOCK = 1800
SAFE_ELEMS_PER_BLOCK = 80

# Pass 1: Regex for markdown [text](evernote://...)
# Use search() not match(), and DOTALL to handle newlines
MARKDOWN_LINK_PATTERN = re.compile(
    r'(?P<prefix>.*?)\[(?P<note_name>[^\]]*)\]\((?P<url>evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)\)(?P<suffix>.*)',
    re.DOTALL
)

# Pass 2: Regex for evernote:// URLs
EVERNOTE_URL_PATTERN = re.compile(r'evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*')


@dataclass
class LinkReference:
    """Reference to an evernote:// link found in a block."""
    page_id: str
    page_title: str
    block_id: str
    block_type: str
    link_text: str
    original_url: str
    rich_text_index: int
    rich_text_item: dict[str, Any]
    pass_type: str  # "markdown" or "href"


def remove_hyphens(page_id: str) -> str:
    """Remove hyphens from page ID."""
    return page_id.replace("-", "")


def count_total_evernote_markdown_links(blocks: list[dict[str, Any]]) -> int:
    """Count total number of markdown evernote links in page for validation.
    
    This scans ALL text content across all blocks to count [text](evernote://...) patterns.
    Used to validate that we find and process all links.
    """
    total = 0
    markdown_pattern = r'\[([^\]]*)\]\((evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)\)'
    
    for block in blocks:
        block_type = block.get("type")
        if block_type and block_type in RICH_TEXT_BLOCK_TYPES:
            block_content = block.get(block_type, {})
            rich_text_array = block_content.get("rich_text", [])
            
            for rich_text_item in rich_text_array:
                if rich_text_item.get("type") == "text":
                    text_content = rich_text_item.get("text", {}).get("content", "")
                    matches = re.findall(markdown_pattern, text_content)
                    total += len(matches)
        
        # Check children recursively
        if "_children" in block:
            total += count_total_evernote_markdown_links(block["_children"])
    
    return total


def _tokenize_rich_text_items(rich_text_array: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tokenize a rich_text array into indivisible units preserving annotations.
    Tokens:
      - {type: "text", content: str, annotations: {...}, is_link: bool}
      - {type: "other", item: original_non_text_item}
    We mark markdown link substrings inside text items as is_link=True to avoid splitting.
    """
    tokens: list[dict[str, Any]] = []
    md = re.compile(r"\[([^\]]*)\]\((evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)\)")
    for item in rich_text_array:
        if item.get("type") != "text":
            tokens.append({"type": "other", "item": item})
            continue
        annotations = item.get("annotations", {})
        text = item.get("text", {}).get("content", "")
        pos = 0
        for m in md.finditer(text):
            if m.start() > pos:
                tokens.append({"type": "text", "content": text[pos:m.start()], "annotations": annotations, "is_link": False})
            tokens.append({"type": "text", "content": text[m.start():m.end()], "annotations": annotations, "is_link": True})
            pos = m.end()
        if pos < len(text):
            tokens.append({"type": "text", "content": text[pos:], "annotations": annotations, "is_link": False})
    return tokens


def _pack_tokens_into_chunks(tokens: list[dict[str, Any]], char_limit: int = SAFE_CHAR_PER_BLOCK, elem_limit: int = SAFE_ELEMS_PER_BLOCK) -> list[list[dict[str, Any]]]:
    """Greedily pack tokens into chunks under limits; never split link tokens."""
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_chars = 0
    cur_elems = 0
    for tok in tokens:
        # element contribution
        elem_inc = 1 if tok["type"] == "other" or tok["type"] == "text" else 1
        text_len = len(tok.get("content", "")) if tok["type"] == "text" else 0
        would_split = tok.get("is_link", False) and text_len > char_limit
        if would_split:
            # extremely long single link; just place it alone in its own chunk
            if cur:
                chunks.append(cur)
                cur, cur_chars, cur_elems = [], 0, 0
            chunks.append([tok])
            continue
        # can we add to current?
        if cur and (cur_chars + text_len > char_limit or cur_elems + elem_inc > elem_limit):
            chunks.append(cur)
            cur, cur_chars, cur_elems = [], 0, 0
        cur.append(tok)
        cur_chars += text_len
        cur_elems += elem_inc
    if cur:
        chunks.append(cur)
    return chunks


def _build_rich_text_from_tokens(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a rich_text array from packed tokens, consolidating adjacent text with same annotations and <=2000 chars."""
    elems: list[dict[str, Any]] = []
    def append_text(content: str, annotations: dict):
        if elems and elems[-1].get("type") == "text" and elems[-1].get("annotations") == annotations:
            prev = elems[-1]["text"]["content"]
            if len(prev) + len(content) <= MAX_RICH_TEXT_LENGTH:
                elems[-1]["text"]["content"] = prev + content
                return
        elems.append(_make_text(content, annotations))
    for tok in tokens:
        if tok["type"] == "other":
            elems.append(tok["item"])  # passthrough mentions etc.
        else:
            append_text(tok["content"], tok.get("annotations", {}))
    return elems


def needs_normalization(block: dict[str, Any]) -> bool:
    bt = block.get("type")
    if not bt or bt not in RICH_TEXT_BLOCK_TYPES:
        return False
    rt = block.get(bt, {}).get("rich_text", [])
    if not rt:
        return False
    # Any individual text element over limit?
    for it in rt:
        if it.get("type") == "text":
            content = it.get("text", {}).get("content", "")
            if len(content) > MAX_RICH_TEXT_LENGTH:
                return True
    # Estimate array size after mention conversion: <= ~100
    if len(rt) > SAFE_ELEMS_PER_BLOCK:
        return True
    # Estimate char length
    total_chars = sum(len(it.get("text", {}).get("content", "")) for it in rt if it.get("type") == "text")
    return total_chars > SAFE_CHAR_PER_BLOCK


def normalize_block_to_safe_chunks(wrapper, block: dict[str, Any]):
    """Replace an oversized rich_text block with multiple safe-sized blocks of same type.
    Order: update original block with first chunk; append remaining chunks to the parent; if append fails, keep original only.
    Note: Appended chunks are added at the end of parent children due to Notion API constraints.
    """
    bt = block.get("type")
    parent = block.get("parent", {})
    parent_id = parent.get("page_id") or parent.get("block_id")
    if not parent_id:
        return
    rt = block.get(bt, {}).get("rich_text", [])
    tokens = _tokenize_rich_text_items(rt)
    chunks = _pack_tokens_into_chunks(tokens)
    if not chunks or (len(chunks) == 1 and not needs_normalization(block)):
        return
    # Build first chunk into original block
    first_rt = _build_rich_text_from_tokens(chunks[0])
    first_rt = _split_all_oversized_elements(first_rt)
    try:
        wrapper.update_block(block["id"], {bt: {"rich_text": first_rt}})
    except Exception:
        return
    # Append remaining chunks as new sibling blocks (at end)
    if len(chunks) > 1:
        new_children = []
        for ch in chunks[1:]:
            ch_rt = _build_rich_text_from_tokens(ch)
            ch_rt = _split_all_oversized_elements(ch_rt)
            new_children.append({
                "object": "block",
                "type": bt,
                bt: {"rich_text": ch_rt},
            })
        try:
            wrapper.append_blocks(parent_id, new_children)
        except Exception:
            pass


def normalize_page_blocks(wrapper, page_id: str):
    """Pass 0: ensure all rich_text blocks under page are within safe thresholds."""
    try:
        blocks = wrapper.get_blocks(page_id)
        # BFS through hierarchy; normalize eligible blocks only
        queue = list(blocks)
        while queue:
            blk = queue.pop(0)
            if needs_normalization(blk):
                normalize_block_to_safe_chunks(wrapper, blk)
            # traverse children if present
            if blk.get("has_children"):
                for ch in blk.get("_children", []) or []:
                    queue.append(ch)
    except Exception as e:
        logger.warning(f"Normalization failed for page {page_id}: {e}")


def find_evernote_links_in_page(
    page_id: str, page_title: str, blocks: list[dict[str, Any]]
) -> list[LinkReference]:
    """Find all evernote:// links in a page's blocks (two-pass detection).
    
    Returns a list of LinkReference objects for detected links.
    Note: This may find fewer links than count_total_evernote_markdown_links if
    multiple links exist in a single rich_text element (current limitation).
    """
    links = []
    for block in blocks:
        block_links = _scan_block_for_links(page_id, page_title, block)
        links.extend(block_links)
        if "_children" in block:
            nested_links = find_evernote_links_in_page(page_id, page_title, block["_children"])
            links.extend(nested_links)
    return links


def _scan_block_for_links(page_id: str, page_title: str, block: dict[str, Any]) -> list[LinkReference]:
    """Scan a single block for evernote:// links (two passes).
    
    Finds ALL links using finditer to support multiple links per rich_text element.
    """
    block_type = block.get("type")
    block_id = block.get("id")
    
    if not block_type or block_type not in RICH_TEXT_BLOCK_TYPES:
        return []
    
    block_content = block.get(block_type, {})
    rich_text_array = block_content.get("rich_text", [])
    
    if not rich_text_array:
        return []
    
    links = []
    
    for idx, rich_text_item in enumerate(rich_text_array):
        if rich_text_item.get("type") != "text":
            continue
        
        text_content = rich_text_item.get("text", {}).get("content", "")
        href = rich_text_item.get("href", "")
        
        # PASS 1: Find ALL markdown format links [note-name](evernote://url)
        markdown_pattern = r'\[([^\]]*)\]\((evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)\)'
        markdown_matches = list(re.finditer(markdown_pattern, text_content))
        
        if markdown_matches:
            # Create a LinkReference for EACH found link
            for match in markdown_matches:
                note_name = match.group(1)
                url = match.group(2)
                links.append(LinkReference(
                    page_id=page_id, page_title=page_title, block_id=block_id,
                    block_type=block_type, link_text=note_name, original_url=url,
                    rich_text_index=idx, rich_text_item=rich_text_item, pass_type="markdown",
                ))
                logger.debug(f"Pass 1: [{note_name}]({url})")
        
        # PASS 2: evernote:// in href attribute (only if no markdown links found)
        elif href and EVERNOTE_URL_PATTERN.match(href):
            note_name = rich_text_item.get("plain_text", text_content)
            links.append(LinkReference(
                page_id=page_id, page_title=page_title, block_id=block_id,
                block_type=block_type, link_text=note_name, original_url=href,
                rich_text_index=idx, rich_text_item=rich_text_item, pass_type="href",
            ))
            logger.debug(f"Pass 2: '{note_name}' -> {href}")
    
    return links


def create_updated_rich_text(
    original_rich_text: list[dict[str, Any]],
    link_ref: LinkReference,
    target_page_id: str | None
) -> list[dict[str, Any]]:
    """Create updated rich_text with evernote:// link converted to inline page link."""
    if link_ref.rich_text_index >= len(original_rich_text):
        logger.error(f"Invalid rich_text_index {link_ref.rich_text_index}")
        return original_rich_text
    
    item = original_rich_text[link_ref.rich_text_index]
    annotations = item.get("annotations", {})
    
    if link_ref.pass_type == "markdown":
        # Pass 1: Split into prefix + link + suffix
        content = item.get("text", {}).get("content", "")
        new_elements = _convert_markdown_link(
            content, link_ref.link_text, target_page_id, annotations
        )
        result = (
            original_rich_text[:link_ref.rich_text_index] +
            new_elements +
            original_rich_text[link_ref.rich_text_index + 1:]
        )
    elif link_ref.pass_type == "href":
        # Pass 2: Single element replacement
        new_element = _convert_href_link(link_ref.link_text, target_page_id, annotations)
        result = (
            original_rich_text[:link_ref.rich_text_index] +
            [new_element] +
            original_rich_text[link_ref.rich_text_index + 1:]
        )
    else:
        result = original_rich_text
    
    # Split any oversized text elements in the entire array
    return _split_all_oversized_elements(result)


def _convert_markdown_link(
    content: str, note_name: str, target_page_id: str | None, annotations: dict
) -> list[dict[str, Any]]:
    """Convert markdown [note-name](evernote://url) to inline page link elements.
    
    DEPRECATED: This function only handles ONE link at a time. Use _convert_text_with_all_links instead
    for text containing multiple links.
    """
    match = MARKDOWN_LINK_PATTERN.search(content)
    if not match:
        # Split if over limit
        return _split_text_if_needed(content, annotations)
    
    prefix = match.group("prefix")
    note_name = match.group("note_name")
    url = match.group("url")
    suffix = match.group("suffix")
    result = []
    
    if prefix:
        result.extend(_split_text_if_needed(prefix, annotations))
    
    if target_page_id:
        # Resolved: create page mention
        result.append(_make_inline_link(note_name, target_page_id, annotations))
    else:
        # Unresolved: keep original markdown text with marker in front
        original_md = f"[{note_name}]({url})"
        unresolved_text = f"🛑 unresolved: {original_md}"
        result.extend(_split_text_if_needed(unresolved_text, annotations))
    
    if suffix:
        # No recursion - suffix handled separately
        result.extend(_split_text_if_needed(suffix, annotations))
    
    return result


def _has_consecutive_links(content: str) -> bool:
    """Check if content contains consecutive evernote links with only whitespace between.
    
    Returns True if we find pattern: link + whitespace + link (with no other text).
    This indicates the content is a list of links that should be split into separate blocks.
    """
    markdown_pattern = r'\[([^\]]*)\]\((evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)\)'
    matches = list(re.finditer(markdown_pattern, content))
    
    if len(matches) < 2:
        return False
    
    # Check if there's only whitespace between consecutive links
    for i in range(len(matches) - 1):
        current_end = matches[i].end()
        next_start = matches[i + 1].start()
        between = content[current_end:next_start]
        
        # If there's text (not just whitespace) between links, they're inline
        if between.strip():
            return False
    
    # Check if there's significant text before first or after last link
    prefix = content[:matches[0].start()].strip()
    suffix = content[matches[-1].end():].strip()
    
    # If there's text surrounding the link sequence, they're inline
    if prefix or suffix:
        return False
    
    # All links are consecutive with only whitespace between
    return True


def _convert_text_with_all_links(
    content: str,
    annotations: dict,
    link_lookup: dict[str, str | None]
) -> list[dict[str, Any]]:
    """Recursively convert ALL markdown evernote links in text to page mentions.
    
    Args:
        content: The text content that may contain multiple [text](evernote://...) links
        annotations: Formatting to preserve
        link_lookup: Dict mapping link_text (case-insensitive) -> target_page_id or None
    
    Returns:
        - List of rich_text elements if links are inline
        - String "SPLIT_TO_BLOCKS" if consecutive links detected (caller should split)
    """
# We currently keep consecutive links inline; splitting to separate blocks is handled by pre-normalization (Pass 0)
    # _has_consecutive_links(content) can be used to branch in the future
    
    markdown_pattern = r'\[([^\]]*)\]\((evernote[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]*)\)'
    match = re.search(markdown_pattern, content)
    
    if not match:
        # No more links - return remaining text
        return _split_text_if_needed(content, annotations)
    
    # Extract parts
    start_pos = match.start()
    end_pos = match.end()
    prefix = content[:start_pos]
    link_text = match.group(1)
    url = match.group(2)
    suffix = content[end_pos:]
    
    result = []
    
    # Add prefix text if exists
    if prefix:
        result.extend(_split_text_if_needed(prefix, annotations))
    
    # Look up target page ID (case-insensitive)
    target_page_id = link_lookup.get((link_text or "").lower())
    
    # Add link/mention
    if target_page_id:
        # Resolved: create page mention
        result.append(_make_inline_link(link_text, target_page_id, annotations))
    else:
        # Unresolved: add marker
        original_md = f"[{link_text}]({url})"
        unresolved_text = f"🛑 unresolved: {original_md}"
        result.extend(_split_text_if_needed(unresolved_text, annotations))
    
    # Recursively process suffix (may contain more links)
    if suffix:
        suffix_elements = _convert_text_with_all_links(suffix, annotations, link_lookup)
        result.extend(suffix_elements)
    
    # Consolidate adjacent text elements to reduce array size
    return _consolidate_adjacent_text(result)


def _consolidate_adjacent_text(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent text elements with same annotations to reduce array size.
    
    Notion limits:
    - ~100 elements per rich_text array
    - 2000 chars per text element
    
    This merges adjacent text with same formatting while respecting the 2000 char limit.
    """
    if not elements:
        return elements
    
    consolidated = []
    i = 0
    
    while i < len(elements):
        current = elements[i]
        
        if current.get("type") == "text":
            # Try to merge with following text elements (up to 2000 char limit)
            merged_content = current["text"]["content"]
            j = i + 1
            
            while j < len(elements):
                next_elem = elements[j]
                if (next_elem.get("type") == "text" and 
                    next_elem.get("annotations") == current.get("annotations")):
                    next_content = next_elem["text"]["content"]
                    # Check if merging would exceed 2000 char limit
                    if len(merged_content) + len(next_content) <= MAX_RICH_TEXT_LENGTH:
                        # Safe to merge
                        merged_content += next_content
                        j += 1
                    else:
                        # Would exceed limit - stop merging
                        break
                else:
                    # Different type or annotations - stop merging
                    break
            
            # Create merged element
            merged = current.copy()
            merged["text"] = {"content": merged_content, "link": None}
            consolidated.append(merged)
            i = j
        else:
            # Non-text element (mention) - keep as-is
            consolidated.append(current)
            i += 1
    
    return consolidated


def _convert_href_link(note_name: str, target_page_id: str | None, annotations: dict) -> dict[str, Any]:
    """Convert href-based evernote link to inline page link."""
    if target_page_id:
        return _make_inline_link(note_name, target_page_id, annotations)
    else:
        return _make_text(f"🛑 unresolved: {note_name}", annotations)


def _split_all_oversized_elements(rich_text_array: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split any oversized text elements in the rich_text array.
    
    This ensures the entire array complies with Notion's 2000 char limit per element.
    Preserves non-text types like mentions.
    """
    result = []
    for item in rich_text_array:
        # Preserve mentions and other non-text types as-is
        if item.get("type") != "text":
            result.append(item)
            continue
        
        content = item.get("text", {}).get("content", "")
        if len(content) <= MAX_RICH_TEXT_LENGTH:
            result.append(item)
        else:
            # Split this oversized element
            annotations = item.get("annotations", {})
            link = item.get("text", {}).get("link")
            if link:
                # Can't split a link - just truncate with warning
                logger.warning(f"Link text exceeds {MAX_RICH_TEXT_LENGTH} chars, truncating: {content[:50]}...")
                result.append(item)
            else:
                # Split plain text
                split_elements = _split_text_if_needed(content, annotations)
                result.extend(split_elements)
    
    return result


def _split_text_if_needed(content: str, annotations: dict | None = None) -> list[dict[str, Any]]:
    """Split text into multiple rich_text elements if it exceeds Notion's limit.
    
    Notion has a 2000 character limit per rich_text element. We split on word
    boundaries to avoid breaking words.
    """
    if len(content) <= MAX_RICH_TEXT_LENGTH:
        return [_make_text(content, annotations)]
    
    # Split into chunks at word boundaries
    chunks = []
    current_chunk = ""
    
    words = content.split(" ")
    for word in words:
        # Check if adding this word would exceed limit
        test_chunk = current_chunk + (" " if current_chunk else "") + word
        if len(test_chunk) > MAX_RICH_TEXT_LENGTH:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = word
            else:
                # Single word exceeds limit - force split
                chunks.append(word[:MAX_RICH_TEXT_LENGTH])
                current_chunk = word[MAX_RICH_TEXT_LENGTH:]
        else:
            current_chunk = test_chunk
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return [_make_text(chunk, annotations) for chunk in chunks]


def _make_text(content: str, annotations: dict | None = None) -> dict[str, Any]:
    """Create plain text rich_text element."""
    return {
        "type": "text",
        "text": {"content": content, "link": None},
        "annotations": annotations or {
            "bold": False, "italic": False, "strikethrough": False,
            "underline": False, "code": False, "color": "default",
        },
    }


def _make_inline_link(note_name: str, target_page_id: str, annotations: dict | None = None) -> dict[str, Any]:
    """Create inline page mention (not a hyperlink).
    
    Uses Notion's mention type to create an inline page reference.
    """
    return {
        "type": "mention",
        "mention": {
            "type": "page",
            "page": {"id": target_page_id}
        },
        "annotations": annotations or {
            "bold": False, "italic": False, "strikethrough": False,
            "underline": False, "code": False, "color": "default",
        },
    }
