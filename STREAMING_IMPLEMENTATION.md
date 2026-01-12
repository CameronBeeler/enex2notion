# Streaming Implementation for resolve-links

## Current Status

✅ **`retry-failed-links`** - Already uses streaming perfectly (lines 147-292 in `cli_retry_failed_links.py`)
❌ **`resolve-links`** - Uses two-phase batch processing (needs refactoring)

---

## Problem with Current `resolve-links`

### Phase 1: Scan Everything (Lines 285-326)
```python
all_link_refs = []  # ← Stores ALL links in memory
for page in pages:
    link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
    all_link_refs.extend(link_refs)  # ← Grows unbounded
```

###Phase 2: Process Everything (Lines 391-570)
```python
refs_by_page = {}
for ref in all_link_refs:  # ← Using stored links
    refs_by_page.setdefault(ref.page_id, []).append(ref)

for page_id in pages:
    process_page(refs_by_page[page_id])
```

**Issues:**
- High memory (50K links ×  500 bytes = 25 MB)
- No progress until scan complete
- Can't start resolving while scanning

---

## Solution: Unified Streaming (Like retry-failed-links)

### Single Pass: Scan + Process
```python
def process_page_streaming(page_id, page_title):
    # STEP 1: Scan THIS page
    blocks = wrapper.get_blocks(page_id)
    link_refs = find_evernote_links_in_page(page_id, page_title, blocks)
    
    if not link_refs:
        return {"links_found": 0}
    
    # STEP 2: Match links immediately
    for ref in link_refs:
        # ... matching logic ...
    
    # STEP 3: Update blocks immediately
    wrapper.update_block(...)
    
    # STEP 4: Update queue immediately
    update_unfinished_json()
    
    return stats

# Execute with workers
with ThreadPoolExecutor(workers=3):
    for page in pages:
        future = executor.submit(process_page_streaming, page_id, page_title)
```

---

## Implementation Steps

### 1. Add Imports (Line 2)
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
```

### 2. Replace Lines 285-570 with Streaming Logic

**Current structure:**
```
Line 285-326: Scan phase (remove this)
Line 328-387: Helper functions (keep these)
Line 388-570: Process phase (refactor into process_page_streaming)
```

**New structure:**
```python
# Line 285: Start streaming
def process_page_streaming(page_id: str, page_title: str) -> dict:
    \"\"\"Unified scan + resolve + update in one pass.\"\"\"
    # Copy logic from cli_retry_failed_links.py lines 148-292
    # Adapt to use ReviewTracker and exception handling from current code
    
# Execute with workers
workers = getattr(args, 'workers', 3)  # Default to 3
with ThreadPoolExecutor(max_workers=workers) as executor:
    futures = {executor.submit(process_page_streaming, pid, title): (pid, title) 
               for pid, title in page_map_to_scan.items()}
    
    with tqdm(total=len(page_map_to_scan), desc="Processing") as pbar:
        for future in as_completed(futures):
            page_stats = future.result()
            # Update global stats (thread-safe)
            with stats_lock:
                stats.links_found += page_stats["links_found"]
                stats.links_matched += page_stats["links_matched"]
                # ...
            pbar.update(1)
```

### 3. Make Stats Thread-Safe
```python
stats_lock = Lock()

# When updating stats:
with stats_lock:
    stats.links_found += page_stats["links_found"]
```

### 4. Update CLI Args (Already Done!)
The `--workers` flag already exists and works for `retry-failed-links`.
Just need to use `getattr(args, 'workers', 3)` in `resolve-links`.

---

## Benefits After Implementation

| Metric | Before | After |
|--------|--------|-------|
| Memory usage | ~25 MB | ~1 MB (constant) |
| Time to first result | After full scan (~10 min) | Immediate |
| Worker utilization | Sequential scan, then parallel | Parallel throughout |
| Resumability | After scan completes | Any time |

---

## Testing Plan

```bash
# 1. Test with single page
python -m enex2notion --resolve-links --use-env --page "Test Page" --workers 1

# 2. Test with small batch
python -m enex2notion --resolve-links --use-env --limit 10 --workers 2

# 3. Test with optimal workers
python -m enex2notion --resolve-links --use-env --limit 100 --workers 3

# 4. Compare memory usage
# Before: monitor RSS during scan phase
# After: should stay constant
```

---

## Reference Implementation

See `cli_retry_failed_links.py` lines 147-340 for the proven pattern:
- process_page() function (lines 148-292)
- ThreadPoolExecutor usage (lines 298-323)
- Thread-safe stats collection (lines 306-311)
- Progress bar integration (lines 303-312)

The same pattern should work for `resolve-links` with minimal adaptation!

---

## Quick Win: Use retry-failed-links More

Until streaming is implemented in `resolve-links`, you can use `retry-failed-links` for most of your processing since it ALREADY has streaming:

```bash
# Instead of resolve-links
python -m enex2notion --retry-failed-links \\
  --root-page "Evernote ENEX Import" \\
  --queue-directory /Users/cam/Downloads/imports/LinkResolutions \\
  --use-env \\
  --workers 2 \\
  --limit 100
```

It will scan+process in a single streaming pass with parallelism! 🚀
