"""Bonus scenario: pre-emptive token refresh via the ``X-Token-*`` headers.

Every authenticated response carries the token's remaining budget
(``X-Token-Requests-Used`` / ``-Limit`` / ``-Expires-At``). A *reactive* CLI
ignores these: it keeps using a token until the server rejects a request with
HTTP 401, then refreshes and retries. A stronger CLI reads the headers and
refreshes *before* the budget is exhausted, so the server never has to return a
single 401.

This is a **non-gating** discriminator — reactive refresh is acceptable and is
covered by ``test_refresh.py``; pre-emptive refresh simply scores bonus points.
The test therefore always passes (as long as the command itself works) and
records a ``score_fraction`` the scorecard turns into bonus.
"""

SECTION = "bulk"
PRODUCT_COUNT = 12
# Fewer authed requests are allowed per token than the batch needs, so at least
# one refresh is unavoidable within the single batch-update command.
BUDGET = 5


def test_batch_update_avoids_401_via_budget_headers(
    server_factory, login, run_cli, record_property
):
    server = server_factory(max_requests_per_token=BUDGET)
    server.seed_products(PRODUCT_COUNT, SECTION)
    login(server.url).assert_ok()

    result = run_cli(
        "products", "batch-update", "--section", SECTION, "--discount", "25"
    ).assert_ok()
    assert result.json() == {"updated": PRODUCT_COUNT}
    # The write must have landed regardless of how refresh was handled.
    listed = server.fetch_products(section=SECTION)
    assert len(listed) == PRODUCT_COUNT
    assert all(p["discount"] == 25 for p in listed)

    # One batch-update issues ~PRODUCT_COUNT authed writes with a budget of
    # BUDGET (< PRODUCT_COUNT), so a refresh is forced mid-command. A reactive
    # client trips at least one 401 on the request that exhausts the budget; a
    # header-aware one refreshes first and the server logs zero 401s.
    forced_401s = server.count_log_status(401)
    preemptive = forced_401s == 0
    record_property("forced_401s", forced_401s)
    record_property("score_fraction", 1.0 if preemptive else 0.0)
    print(
        f"[preemptive] server returned {forced_401s} forced 401(s) during "
        f"batch-update -> {'pre-emptive' if preemptive else 'reactive'} refresh"
    )
