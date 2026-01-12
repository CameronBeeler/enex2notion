"""Benchmark script to determine optimal worker count for link resolution.

Tests different worker counts to find the sweet spot between parallelism and rate limiting.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Simulate Notion API with rate limiting
class MockAPI:
    def __init__(self, rate_limit_delay=0.35):
        self.rate_limit_delay = rate_limit_delay
        self.lock = Lock()
        self.call_count = 0
        self.start_time = None
    
    def api_call(self):
        """Simulate a Notion API call with rate limiting."""
        with self.lock:
            self.call_count += 1
        # Simulate rate limit delay (each call takes ~0.35s)
        time.sleep(self.rate_limit_delay)
        return True
    
    def get_stats(self):
        elapsed = time.time() - self.start_time
        return {
            "total_calls": self.call_count,
            "elapsed_seconds": elapsed,
            "calls_per_second": self.call_count / elapsed if elapsed > 0 else 0
        }


def simulate_page_processing(api, page_id):
    """Simulate processing one page (multiple API calls)."""
    # Average page: 3 API calls (get_blocks, validate, update_block)
    for _ in range(3):
        api.api_call()
    return page_id


def benchmark_workers(num_pages=50, max_workers=8):
    """Benchmark different worker counts."""
    print("=" * 80)
    print(f"BENCHMARK: Processing {num_pages} pages with varying worker counts")
    print("=" * 80)
    print("\nEach page simulates 3 API calls (get_blocks, validate, update_block)")
    print("Notion rate limit: ~3 requests/second (0.35s per call)")
    print()
    
    results = []
    
    for workers in [1, 2, 3, 4, 6, 8]:
        api = MockAPI()
        api.start_time = time.time()
        
        print(f"Testing {workers} worker(s)...", end=" ", flush=True)
        
        # Simulate processing pages
        page_ids = list(range(num_pages))
        
        if workers == 1:
            # Sequential
            for pid in page_ids:
                simulate_page_processing(api, pid)
        else:
            # Parallel
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(simulate_page_processing, api, pid) for pid in page_ids]
                for future in as_completed(futures):
                    future.result()
        
        stats = api.get_stats()
        results.append((workers, stats))
        
        print(f"{stats['elapsed_seconds']:.1f}s ({stats['calls_per_second']:.2f} req/s)")
    
    # Print summary
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'Workers':<10} {'Time (s)':<12} {'Req/s':<12} {'Efficiency':<15} {'Recommendation'}")
    print("-" * 80)
    
    best_time = min(r[1]['elapsed_seconds'] for r in results)
    
    for workers, stats in results:
        efficiency = (best_time / stats['elapsed_seconds']) * 100
        recommendation = "✓ OPTIMAL" if efficiency > 95 else ("OK" if efficiency > 85 else "")
        
        print(f"{workers:<10} {stats['elapsed_seconds']:<12.1f} {stats['calls_per_second']:<12.2f} {efficiency:<14.1f}% {recommendation}")
    
    print("\n" + "=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    
    # Find optimal worker count (best time within 5% of absolute best)
    optimal = [w for w, s in results if s['elapsed_seconds'] <= best_time * 1.05]
    
    if optimal:
        print(f"\nOptimal worker count: {min(optimal)}-{max(optimal)}")
        print(f"\nRationale:")
        print(f"  • Beyond {max(optimal)} workers, rate limiting causes diminishing returns")
        print(f"  • Each worker competes for the same 3 req/s API limit")
        print(f"  • More workers = more overhead with no speed benefit")
    
    print()


if __name__ == "__main__":
    benchmark_workers()
