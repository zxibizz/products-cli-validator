"""Performance scenario: `batch-update` over a large section (100 products).

The server pays its default downstream event-bus latency (0.4s) on every write,
so updating 100 products one at a time takes ~40s. A correct CLI must issue the
writes concurrently — while still handling transparent refresh — to finish
within the hard 10-second limit; a naive sequential implementation blows it.
This test seeds a section with 100 products directly in the server's database
(fast, no auth), then runs the CLI's `batch-update` under that deadline.

The server runs with its default token budget, so the ~101 authenticated
requests the batch-update makes also exercise transparent refresh under load.
"""

import concurrent.futures
import time

PRODUCT_COUNT = 100
SECTION = "bulk"
TIME_LIMIT_SECONDS = 10.0


def _perf_bucket(elapsed: float) -> tuple[str, float]:
    """Map an elapsed time to a (label, score-fraction) tier.

    The 10s gate is pass/fail, but timing carries far more signal than one bit:
    with 0.4s latency per write the sequential floor is ~40s and the concurrent
    floor is ~0.4s, so a fully-concurrent solution and a barely-passing one are
    very different candidates. Grading by bucket keeps the hard gate while
    rewarding genuinely strong concurrency at the top end.
    """
    if elapsed < 2.0:
        return "excellent (<2s)", 1.0
    if elapsed < 4.0:
        return "strong (<4s)", 0.85
    if elapsed < 6.0:
        return "good (<6s)", 0.6
    if elapsed < 8.0:
        return "fair (<8s)", 0.4
    if elapsed < TIME_LIMIT_SECONDS:
        return "marginal (<10s)", 0.2
    return "over limit", 0.0


def test_batch_update_100_products_within_time_limit(
    server_factory, login, run_cli, record_property
):
    # The server runs with its default downstream event-bus latency (0.4s),
    # which every authenticated write pays once. batch-update over 100 products
    # issues ~100 writes, so run sequentially that is ~40s — well past the
    # limit. A correct CLI must therefore issue the writes concurrently (while
    # still handling transparent refresh) to finish inside the budget; a naive
    # one-at-a-time implementation blows it.
    server = server_factory(downstream_event_bus_latency_seconds=0.4)
    server.seed_products(PRODUCT_COUNT, SECTION)

    login(server.url).assert_ok()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        start = time.perf_counter()
        future = executor.submit(
            run_cli,
            "products",
            "batch-update",
            "--section",
            SECTION,
            "--discount",
            "30",
        )
        try:
            result = future.result(timeout=TIME_LIMIT_SECONDS)
        except concurrent.futures.TimeoutError:
            # Exceeding the limit is not a hard failure: it just scores zero.
            # Don't block the suite waiting on the leaked worker.
            executor.shutdown(wait=False)
            record_property("perf_elapsed", round(TIME_LIMIT_SECONDS, 2))
            record_property("perf_bucket", "over limit")
            record_property("score_fraction", 0.0)
            print(
                f"[perf] batch-update {PRODUCT_COUNT} products: exceeded the "
                f"{TIME_LIMIT_SECONDS:.0f}s limit -> over limit (score 0%)"
            )
            return
        elapsed = time.perf_counter() - start

    result.assert_ok()
    assert result.json() == {"updated": PRODUCT_COUNT}

    # Verify the discount was actually applied across the whole section in the DB.
    listed = server.fetch_products(section=SECTION)
    assert len(listed) == PRODUCT_COUNT
    assert all(p["discount"] == 30 for p in listed)

    # Record the graded tier only after correctness is established, so a fast
    # but racy/incorrect implementation scores zero rather than banking speed.
    bucket, fraction = _perf_bucket(elapsed)
    record_property("perf_elapsed", round(elapsed, 2))
    record_property("perf_bucket", bucket)
    record_property("score_fraction", fraction)
    print(
        f"[perf] batch-update {PRODUCT_COUNT} products: {elapsed:.2f}s "
        f"-> {bucket} (score {fraction:.0%})"
    )
