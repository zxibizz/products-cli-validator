"""Bonus scenario: defensive client-side input validation.

``products update`` with no field options describes no change. A defensive CLI
rejects it **client-side** — a clear message, a non-zero exit, empty stdout, no
traceback — instead of issuing a pointless request or echoing an unchanged
record.

This is a **non-gating** discriminator: sending an empty update to the server is
not wrong, it just scores no bonus here. The check records a ``score_fraction``
the scorecard turns into bonus.
"""

_TRACEBACK_MARKER = "Traceback (most recent call last)"


def test_update_with_no_fields_is_rejected(server, login, run_cli, record_property):
    login(server.url).assert_ok()

    result = run_cli("products", "update", "--id", "1")
    validated = (
        result.exit_code != 0
        and result.stdout.strip() == ""
        and _TRACEBACK_MARKER not in result.stdout
        and _TRACEBACK_MARKER not in result.stderr
    )
    record_property("score_fraction", 1.0 if validated else 0.0)
    print(
        f"[validation] empty update -> exit={result.exit_code} "
        f"stdout_empty={result.stdout.strip() == ''} validated={validated}"
    )
