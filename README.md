# Products CLI Validator

> **For candidates:** If you found this repo, nice work — that kind of
> resourcefulness is exactly what we look for. Feel free to use it to run these
> validations against your own solution before you submit.

Container-based validation harness for the **Products CLI** take-home assignment.

Given the `.tar.gz` a candidate produces with the assignment's
`package_submission.py`, this repo:

1. **Unpacks** the archive safely into `submissions/`.
2. **Builds two containers** from the submission — the reference **server**
   (from `server/Dockerfile`) and the candidate's **CLI** (their `cli/` project,
   installed with `uv`).
3. **Runs the CLI against the server** inside Docker (using
   [testcontainers](https://testcontainers.com/)) and asserts every documented
   and hidden scenario: login, listing + filters, get/update, batch-update,
   error handling, and — most importantly — **transparent token refresh**.

Nothing is installed on the host and no ports are hard-coded: the CLI reaches
the server over a private Docker network at `http://api:8000`.

## Prerequisites

| Tool   | Why                                             |
|--------|-------------------------------------------------|
| Docker | Builds and runs the server + CLI containers.    |
| uv     | Manages this harness's own Python environment.  |

Docker must be running. The first run pulls `python:3.12-slim` and the `uv`
image and resolves the candidate's dependencies, so it needs network access.

## Usage

```bash
# one-shot: extract the archive and run the whole suite
uv run python validate.py path/to/trainee_assignment-jane-doe-20260722-120000.tar.gz

# just unpack (no tests)
uv run python validate.py submission.tar.gz --extract-only

# forward extra args to pytest (after a --)
uv run python validate.py submission.tar.gz -- -k refresh
uv run python validate.py submission.tar.gz -- -k "list or update"
```

The archive is extracted to `submissions/<name>/` (name derived from the archive
filename, or `--name`). `validate.py` then invokes pytest with `SUBMISSION_DIR`
pointing at the extracted submission, and records that path in
`.current_submission` for subsequent bare `pytest` runs.

### Running pytest directly

```bash
# against the submission recorded in .current_submission (written by validate.py)
uv run pytest

# against a specific one (overrides .current_submission)
SUBMISSION_DIR=submissions/jane-doe uv run pytest
```

If `SUBMISSION_DIR` is unset and no `.current_submission` file exists, pytest
fails fast telling you to run `validate.py` first.

## What is tested

| File                              | Scenarios                                                                 |
|-----------------------------------|---------------------------------------------------------------------------|
| `tests/test_login.py`             | `login` prints `{"status":"ok"}`; products reuse the stored base URL; bad credentials fail. |
| `tests/test_products_list.py`     | JSON-array output; `section`, `name` (case-insensitive substring), price range, `has-discount`/`no-discount`, `limit`/`offset`, combined filters, inverted range error. |
| `tests/test_products_get_update.py` | `get` by id; missing id fails; `update` fields persist; missing id fails. |
| `tests/test_batch_update.py`      | Discount applied to a whole section; count reported; other sections untouched; empty section. |
| `tests/test_refresh.py`           | Transparent refresh across invocations (request limit), within a single command, on TTL expiry, and far past the default budget. |
| `tests/test_preemptive_refresh.py` | **Bonus:** with a tight budget, a batch-update that forces a mid-command refresh must produce **zero** server-side 401s — only a client that reads the `X-Token-*` budget headers and refreshes pre-emptively scores here (reactive refresh is fine, just not bonus). |
| `tests/test_performance.py`       | `batch-update` over a section of 100 products, graded by elapsed time into tiers (`<2s` excellent … `<10s` marginal) so strong concurrency scores above a barely-passing run; exceeding the 10-second limit scores 0 rather than failing the run. |
| `tests/test_create_delete.py` | `create` then `delete` round-trip (as admin); non-admin `delete` is refused. |
| `tests/test_bonus_error_hygiene.py` | **Bonus:** on a 404 and on bad credentials the CLI must fail cleanly — message on stderr, empty stdout, non-zero exit, **no** raw Python traceback. |
| `tests/test_bonus_network_resilience.py` | **Bonus:** `login` against an unreachable host (`*.invalid`) must fail fast and politely within ~20s — clear stderr message, empty stdout, no traceback, **no hang**. |
| `tests/test_bonus_input_validation.py` | **Bonus:** `update` with no field options is rejected **client-side** (non-zero exit, empty stdout, no traceback) rather than issuing a pointless request. |

Refresh tests start dedicated server containers with a tightened
`MAX_REQUESTS_PER_TOKEN` / `ACCESS_TOKEN_TTL_SECONDS` so refresh is exercised in
seconds rather than requiring 20+ real requests.

The performance test seeds 100 products directly into the server's SQLite
database (a single `exec` inside the container, no auth), then runs the CLI's
`batch-update` under a 10-second deadline (enforced with a worker thread, so a
hung or pathologically slow implementation scores zero rather than blocking the
suite).

## Scorecard

Every run aggregates the per-scenario results into a weighted, machine-readable
score and prints a summary table at the end of the pytest session. Each base
dimension awards `weight × fraction`, where `fraction` is the share of that
module's tests that passed — except graded modules (performance, pre-emptive
refresh) which contribute a tiered `score_fraction` instead of a bare pass/fail.
Base dimensions sum to 100; bonus dimensions add on top (pre-emptive refresh 10,
error-output hygiene 5, network resilience 5, defensive input validation 5 —
25 total), so a polished submission can score up to 125. Bonus scenarios are
non-gating: they always pass and merely record a `score_fraction`, so a plainer
submission stays green and just forgoes the extra points. A filtered run (e.g.
`-- -k refresh`) scores only the dimensions that actually ran and is flagged as
partial. The full breakdown is also written to `last_scorecard.json` for
reproducible grading.

## How it works

- `tests/conftest.py` holds the harness:
  - session-scoped fixtures build the **server** and **CLI** images and start a
    long-lived CLI container on a shared Docker network;
  - a function-scoped `server_factory` starts a **fresh, re-seeded** server for
    each test (the app resets its DB and in-memory auth state on startup), so
    mutating tests don't interfere with each other;
  - `run_cli` execs `uv run products-cli ...` inside the CLI container and
    returns exit code + stdout/stderr, with helpers to assert success/failure
    and parse JSON.
- Each test logs in first, so stored tokens from a previous test are overwritten.

## Notes

- Extraction guards against path-traversal / tarbombs and uses the `data` tar
  filter on Python 3.12+.
- Extracted submissions under `submissions/` are git-ignored.
