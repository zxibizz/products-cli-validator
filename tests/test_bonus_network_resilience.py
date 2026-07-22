"""Bonus scenario: graceful handling of an unreachable server.

Pointing the CLI at a host that cannot be reached should **fail fast and
politely**: a clear message on stderr, a non-zero exit, nothing on stdout, no
raw traceback, and — crucially — **no hang**. A tool with a sensible connect
timeout and error handling satisfies this; one that blocks indefinitely or
crashes with a stack trace does not.

This is a **non-gating** discriminator that always passes; it only records a
``score_fraction`` the scorecard turns into bonus. A worker thread with a
deadline guards the suite against a CLI that never returns.
"""

import concurrent.futures

# RFC 6761 reserves the ``.invalid`` TLD as guaranteed-non-resolvable, so this
# host fails DNS resolution quickly on any network.
_UNREACHABLE_URL = "http://unreachable.invalid:8000"
_TRACEBACK_MARKER = "Traceback (most recent call last)"
_DEADLINE_SECONDS = 20.0


def test_unreachable_server_fails_gracefully(run_cli, record_property):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        run_cli,
        "login",
        "--base-url",
        _UNREACHABLE_URL,
        "--username",
        "demo",
        "--password",
        "password123",
    )
    try:
        result = future.result(timeout=_DEADLINE_SECONDS)
    except concurrent.futures.TimeoutError:
        # The command never returned: a missing connect timeout / hang. Don't
        # block the suite waiting on the leaked worker; score zero and move on.
        executor.shutdown(wait=False)
        record_property("score_fraction", 0.0)
        record_property("note", "command hung past deadline")
        print(
            f"[resilience] login to unreachable host hung > {_DEADLINE_SECONDS}s "
            f"-> no bonus"
        )
        return
    executor.shutdown(wait=False)

    graceful = (
        result.exit_code != 0
        and result.stdout.strip() == ""
        and result.stderr.strip() != ""
        and _TRACEBACK_MARKER not in result.stdout
        and _TRACEBACK_MARKER not in result.stderr
    )
    record_property("score_fraction", 1.0 if graceful else 0.0)
    print(
        f"[resilience] login to unreachable host -> exit={result.exit_code} "
        f"stdout_empty={result.stdout.strip() == ''} "
        f"stderr_msg={bool(result.stderr.strip())} graceful={graceful}"
    )
