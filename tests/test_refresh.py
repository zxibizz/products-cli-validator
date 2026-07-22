"""Hidden but critical scenario: transparent token refresh.

An access token is limited by both a request budget (``MAX_REQUESTS_PER_TOKEN``)
and a lifetime (``ACCESS_TOKEN_TTL_SECONDS``). When either is exceeded the server
returns HTTP 401 and the CLI must refresh (rotating refresh tokens, persisting
the newest pair) and retry transparently — including across separate CLI
invocations, since each command is its own process.
"""

import time


def test_refresh_across_invocations_on_request_limit(server_factory, login, run_cli):
    # Only ONE authenticated request is allowed per access token.
    server = server_factory(max_requests_per_token=1)
    login(server.url).assert_ok()

    # The first authed request consumes the token; each subsequent invocation
    # must transparently refresh and still succeed.
    for _ in range(5):
        result = run_cli("products", "get", "--id", "1").assert_ok()
        assert result.json()["id"] == 1


def test_refresh_within_a_single_command(server_factory, login, run_cli):
    # batch-update over a 3-product section makes several authed requests in one
    # invocation; with a budget of 2 it must refresh mid-command and finish.
    server = server_factory(max_requests_per_token=2)
    login(server.url).assert_ok()

    result = run_cli(
        "products", "batch-update", "--section", "books", "--discount", "40"
    ).assert_ok()
    assert result.json() == {"updated": 3}

    # The discount must be persisted despite the mid-command refresh.
    books = server.fetch_products(section="books")
    assert len(books) == 3
    assert all(p["discount"] == 40 for p in books)


def test_refresh_on_token_expiry(server_factory, login, run_cli):
    # Token expires after 1s regardless of request count.
    server = server_factory(access_token_ttl_seconds=1, max_requests_per_token=1000)
    login(server.url).assert_ok()

    time.sleep(2)  # let the access token expire

    result = run_cli("products", "get", "--id", "1").assert_ok()
    assert result.json()["id"] == 1


def test_many_sequential_requests_exceed_default_budget(server_factory, login, run_cli):
    # With the default budget of 20, running well past it across invocations
    # must keep working thanks to refresh + persisted rotated tokens.
    server = server_factory(max_requests_per_token=3)
    login(server.url).assert_ok()

    for i in range(10):
        result = run_cli("products", "get", "--id", "1").assert_ok()
        assert result.json()["id"] == 1, f"invocation {i} failed"
