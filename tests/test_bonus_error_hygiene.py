"""Bonus scenario: error output hygiene.

A polished CLI reports failures cleanly: a human-readable message on **stderr**,
a **non-zero** exit, **nothing on stdout** (so a downstream ``... | jq`` never
sees half a document or an error string), and **no raw Python traceback**
leaking the tool's internals.

This is a **non-gating** discriminator — a submission that dumps a traceback or
writes its error to stdout still works and stays green; it simply scores no bonus
here. Each check records a ``score_fraction`` and the scorecard averages them
into bonus points.
"""

_TRACEBACK_MARKER = "Traceback (most recent call last)"


def _is_clean_error(result) -> bool:
    return (
        result.exit_code != 0
        and result.stdout.strip() == ""
        and result.stderr.strip() != ""
        and _TRACEBACK_MARKER not in result.stdout
        and _TRACEBACK_MARKER not in result.stderr
    )


def test_missing_product_error_is_clean(server, login, run_cli, record_property):
    """Fetching a non-existent product should fail cleanly (message on stderr,
    empty stdout, non-zero exit, no traceback)."""
    login(server.url).assert_ok()

    result = run_cli("products", "get", "--id", "999999")
    clean = _is_clean_error(result)
    record_property("score_fraction", 1.0 if clean else 0.0)
    print(
        f"[hygiene] get missing id -> exit={result.exit_code} "
        f"stdout_empty={result.stdout.strip() == ''} "
        f"stderr_msg={bool(result.stderr.strip())} clean={clean}"
    )


def test_bad_credentials_error_is_clean(server, run_cli, record_property):
    """Logging in with a wrong password should fail cleanly, the same way."""
    result = run_cli(
        "login",
        "--base-url",
        server.url,
        "--username",
        "demo",
        "--password",
        "wrong-password",
    )
    clean = _is_clean_error(result)
    record_property("score_fraction", 1.0 if clean else 0.0)
    print(
        f"[hygiene] bad login -> exit={result.exit_code} "
        f"stdout_empty={result.stdout.strip() == ''} "
        f"stderr_msg={bool(result.stderr.strip())} clean={clean}"
    )
