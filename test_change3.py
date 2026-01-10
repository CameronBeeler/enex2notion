#!/usr/bin/env python3
"""Quick test for Change 3: single-character title link resolution."""

from enex2notion.link_resolver import _convert_text_with_all_links


def run():
    text = "[C](evernote:///view/6209675/s52/ddff1db5-dc05-4116-bd7a-9f5007d33377/ddff1db5-dc05-4116-bd7a-9f5007d33377/), other text"
    annotations = {}
    # link_lookup uses normalized keys (casefold + collapse spaces)
    link_lookup = {"c": "00000000-0000-0000-0000-0000000000c0"}
    elems = _convert_text_with_all_links(text, annotations, link_lookup)
    # Expect first element to be a mention
    has_mention = any(e.get("type") == "mention" and e.get("mention", {}).get("type") == "page" for e in elems)
    print("Mention created:", has_mention)
    if not has_mention:
        print("Elements:", elems)
    return 0 if has_mention else 1

if __name__ == "__main__":
    raise SystemExit(run())
