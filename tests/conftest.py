"""Container-based test harness for validating a candidate's Products CLI.

The harness builds two images from the submission:

* **server** — from ``<submission>/server/Dockerfile`` (the reference API).
* **cli**    — the candidate's ``<submission>/cli`` project, installed with uv.

Both run on a shared Docker network. The server is reachable from the CLI
container at ``http://api:8000``. Tests drive the CLI by exec-ing
``uv run products-cli ...`` inside the CLI container and asserting on its exit
code, stdout (which must be valid JSON on success) and stderr.

The submission under test is selected via the ``SUBMISSION_DIR`` environment
variable (set by ``validate.py``); if unset, the most recently modified folder
under ``submissions/`` is used.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import docker
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBMISSIONS_DIR = REPO_ROOT / "submissions"
# Pointer file written by validate.py recording the submission to test when
# SUBMISSION_DIR is not set.
CURRENT_SUBMISSION_FILE = REPO_ROOT / ".current_submission"

SERVER_ALIAS = "api"
SERVER_URL = f"http://{SERVER_ALIAS}:8000"

SERVER_IMAGE_TAG = "products-cli-server:test"
CLI_IMAGE_TAG = "products-cli:test"

# Dockerfile used to package the candidate CLI. It is written into the CLI
# directory at build time so that ``COPY .`` uses the project as build context.
CLI_DOCKERFILE = """\
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY . /app
# Install the candidate project + deps. Prefer the committed lockfile; fall
# back to a fresh resolution if the lock is missing or out of date.
RUN uv sync --frozen || uv sync
# Keep the container alive so tests can exec commands into it.
CMD ["tail", "-f", "/dev/null"]
"""

DEMO_USER = "demo"
DEMO_PASSWORD = "password123"

# Admin account. The server restricts privileged actions (e.g. deleting a
# product) to users holding the ADMIN role; the demo ``demo`` user does not.
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"


# --------------------------------------------------------------------------- #
# Submission discovery
# --------------------------------------------------------------------------- #
def _resolve_submission_dir() -> Path:
    env = os.environ.get("SUBMISSION_DIR")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise pytest.UsageError(f"SUBMISSION_DIR does not exist: {root}")
    else:
        if not CURRENT_SUBMISSION_FILE.exists():
            raise pytest.UsageError(
                "No submission selected. Run `python validate.py <archive>` first "
                "(it records the submission in .current_submission), or set "
                "SUBMISSION_DIR."
            )
        pointer = CURRENT_SUBMISSION_FILE.read_text().strip()
        if not pointer:
            raise pytest.UsageError(
                f"{CURRENT_SUBMISSION_FILE.name} is empty; re-run validate.py or set "
                "SUBMISSION_DIR."
            )
        root = Path(pointer).expanduser().resolve()
        if not root.is_dir():
            raise pytest.UsageError(
                f"{CURRENT_SUBMISSION_FILE.name} points to a missing directory: {root}"
            )

    # Allow SUBMISSION_DIR to point either at the root or one level above it.
    if (root / "server").is_dir() and (root / "cli").is_dir():
        return root
    for child in root.iterdir():
        if child.is_dir() and (child / "server").is_dir() and (child / "cli").is_dir():
            return child
    raise pytest.UsageError(f"Could not find 'server/' and 'cli/' under {root}.")


def pytest_configure(config: pytest.Config) -> None:
    """Resolve the submission once, up front, so a missing/invalid selection
    aborts the session with a single message instead of erroring per test."""
    config._submission_dir = _resolve_submission_dir()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Session-scoped infrastructure: docker client, images, network, CLI container
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def submission_dir(pytestconfig: pytest.Config) -> Path:
    return pytestconfig._submission_dir  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def docker_client() -> docker.DockerClient:
    return docker.from_env()


@pytest.fixture(scope="session")
def server_image(docker_client: docker.DockerClient, submission_dir: Path) -> str:
    server_dir = submission_dir / "server"
    print(f"\n[build] server image from {server_dir} ...")
    docker_client.images.build(
        path=str(server_dir), tag=SERVER_IMAGE_TAG, rm=True, pull=False
    )
    return SERVER_IMAGE_TAG


@pytest.fixture(scope="session")
def cli_image(docker_client: docker.DockerClient, submission_dir: Path) -> str:
    cli_dir = submission_dir / "cli"
    dockerfile = cli_dir / "Dockerfile.validation"
    dockerfile.write_text(CLI_DOCKERFILE)
    print(f"\n[build] cli image from {cli_dir} ...")
    try:
        docker_client.images.build(
            path=str(cli_dir),
            dockerfile=dockerfile.name,
            tag=CLI_IMAGE_TAG,
            rm=True,
            pull=False,
        )
    finally:
        dockerfile.unlink(missing_ok=True)
    return CLI_IMAGE_TAG


@pytest.fixture(scope="session")
def network():
    net = Network()
    net.create()
    try:
        yield net
    finally:
        net.remove()


@pytest.fixture(scope="session")
def cli_container(cli_image: str, network):
    container = (
        DockerContainer(cli_image)
        .with_network(network)
        .with_command("tail -f /dev/null")
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


# --------------------------------------------------------------------------- #
# Function-scoped server: fresh, seeded DB + fresh auth state for every test
# --------------------------------------------------------------------------- #
@dataclass
class Server:
    """A running server container: its base URL plus test helpers.

    ``seed_products`` writes straight into the server's SQLite database (inside
    the container) rather than going through the authenticated API, so large
    amounts of data can be staged with no login/refresh bookkeeping.
    """

    url: str  # base URL the CLI container uses (docker network alias)
    container: DockerContainer

    # Prelude prepended to every DB snippet: imports plus an open connection
    # ``conn`` (row_factory=Row) against the app's *configured* database path,
    # so the helpers keep working even if a candidate changed DATABASE_PATH.
    _DB_PRELUDE = (
        "import os, sqlite3, json;"
        "from app.config import settings;"
        "conn=sqlite3.connect(settings.database_path);"
        "conn.row_factory=sqlite3.Row;"
    )

    def seed_products(self, count: int, section: str, price: float = 10.0) -> None:
        # Bulk-insert rows straight into the DB (no login/refresh bookkeeping).
        self._execute_sql(
            "n=int(os.environ['SEED_COUNT']); s=os.environ['SEED_SECTION'];"
            "p=float(os.environ['SEED_PRICE']);"
            "conn.executemany("
            "'INSERT INTO products (name, section, description, discount, price)"
            " VALUES (?,?,?,?,?)',"
            "[(f'Bulk Item {i}', s, '', 0.0, p) for i in range(n)]);"
            "conn.commit(); conn.close()",
            {
                "SEED_COUNT": str(count),
                "SEED_SECTION": section,
                "SEED_PRICE": str(price),
            },
        )

    def fetch_product(self, product_id: int) -> dict | None:
        """Read one product row straight from the server's SQLite database.

        This bypasses the candidate's CLI *and* the HTTP API, so tests can
        assert on what was actually persisted rather than trusting the tool
        under test to report its own writes truthfully. Returns the row as a
        dict, or ``None`` if no product with that id exists.
        """
        return self._read_json(
            "row=conn.execute('SELECT * FROM products WHERE id=?',"
            " (int(os.environ['PRODUCT_ID']),)).fetchone();"
            "conn.close();"
            "print(json.dumps(dict(row) if row is not None else None))",
            {"PRODUCT_ID": str(product_id)},
        )

    def fetch_products(self, *, section: str | None = None) -> list[dict]:
        """Read product rows straight from the server's SQLite database.

        Bypasses the CLI and HTTP API so tests assert on persisted state.
        Optionally restrict to a single ``section``; rows are ordered by id.
        """
        env = {"SECTION": section} if section is not None else {}
        return self._read_json(
            "sec=os.environ.get('SECTION') or None;"
            "rows=conn.execute("
            "'SELECT * FROM products WHERE (? IS NULL OR section=?) ORDER BY id',"
            " (sec, sec)).fetchall();"
            "conn.close();"
            "print(json.dumps([dict(r) for r in rows]))",
            env,
        )

    def count_log_status(self, status_code: int) -> int:
        """Count responses the server logged with the given HTTP status.

        The server's request middleware logs every response as
        ``<method> <path> -> <status> (<ms> ms)``. Scanning the container logs
        lets a test tell a *pre-emptive* client (which reads the ``X-Token-*``
        budget headers and refreshes before the budget runs out, so the server
        never returns a 401) from a merely *reactive* one (which trips at least
        one forced 401 before refreshing).
        """
        raw = self.container.get_wrapped_container().logs()
        text = (
            raw.decode(errors="replace")
            if isinstance(raw, (bytes, bytearray))
            else str(raw)
        )
        return text.count(f"-> {status_code} (")

    def _execute_sql(self, body: str, env: dict[str, str] | None = None) -> str:
        """Run a DB snippet in the server container and return its stdout.

        ``body`` runs after :attr:`_DB_PRELUDE`, so it may use ``os``, ``json``
        and the open connection ``conn`` directly (and is responsible for
        committing/closing as needed). Asserts the snippet exited 0.
        """
        exit_code, output = self.container.get_wrapped_container().exec_run(
            ["uv", "run", "python", "-c", self._DB_PRELUDE + body],
            workdir="/app",
            environment=env or {},
            demux=True,
        )
        stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
        assert exit_code == 0, (
            f"DB command failed (exit {exit_code}):\n"
            f"{(stderr_b or b'').decode(errors='replace')}"
        )
        return (stdout_b or b"").decode()

    def _read_json(self, body: str, env: dict[str, str] | None = None):
        """Run a DB snippet (see :meth:`_execute_sql`) and parse its JSON stdout."""
        return json.loads(self._execute_sql(body, env))


@pytest.fixture
def server_factory(server_image: str, network) -> Callable[..., Server]:
    """Start a server container and return a :class:`Server` handle.

    This is the single, multi-purpose server fixture. Every server env var is an
    explicit keyword argument with a default convenient for tests, so callers
    override only what a scenario cares about, e.g.
    ``server_factory(max_requests_per_token=1)`` or, for performance tests,
    ``server_factory(downstream_event_bus_latency_seconds=0.05)``. Call
    ``.seed_products(...)`` on the result to stage bulk data.

    Defaults mirror the server's own defaults except
    ``downstream_event_bus_latency_seconds``, which is ``0`` here so the suite
    runs fast; performance tests opt back into a realistic latency explicitly.
    """
    started: list[DockerContainer] = []

    def _start(
        *,
        max_requests_per_token: int = 20,
        access_token_ttl_seconds: int = 60,
        downstream_event_bus_latency_seconds: float = 0.0,
        database_path: str = "products.db",
        jwt_secret: str | None = None,
        jwt_algorithm: str | None = None,
    ) -> Server:
        container = (
            DockerContainer(server_image)
            .with_network(network)
            .with_network_aliases(SERVER_ALIAS)
            .with_env("MAX_REQUESTS_PER_TOKEN", str(max_requests_per_token))
            .with_env("ACCESS_TOKEN_TTL_SECONDS", str(access_token_ttl_seconds))
            .with_env(
                "DOWNSTREAM_EVENT_BUS_LATENCY_SECONDS",
                str(downstream_event_bus_latency_seconds),
            )
            .with_env("DATABASE_PATH", database_path)
        )
        if jwt_secret is not None:
            container.with_env("JWT_SECRET", jwt_secret)
        if jwt_algorithm is not None:
            container.with_env("JWT_ALGORITHM", jwt_algorithm)
        container.start()
        wait_for_logs(container, r"Application startup complete", timeout=90)
        started.append(container)
        return Server(url=SERVER_URL, container=container)

    yield _start

    for container in started:
        try:
            container.stop()
        except Exception:  # pragma: no cover - best-effort teardown
            pass


@pytest.fixture
def server(server_factory: Callable[..., Server]) -> Server:
    """A running reference server (default config). Exposes ``.fetch_product``
    so tests can assert directly against the persisted database."""
    return server_factory()


@pytest.fixture
def base_url(server: Server) -> str:
    """Base URL of the default reference server (see the ``server`` fixture)."""
    return server.url


# --------------------------------------------------------------------------- #
# CLI invocation helper
# --------------------------------------------------------------------------- #
@dataclass
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    args: list[str] = field(default_factory=list)

    def json(self):
        """Parse stdout as JSON (fails the test with context if it isn't)."""
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"stdout was not valid JSON for `products-cli {' '.join(self.args)}`\n"
                f"exit_code={self.exit_code}\n--- stdout ---\n{self.stdout}\n"
                f"--- stderr ---\n{self.stderr}"
            ) from exc

    def assert_ok(self):
        assert self.exit_code == 0, (
            f"expected exit 0 for `products-cli {' '.join(self.args)}` but got "
            f"{self.exit_code}\n--- stdout ---\n{self.stdout}\n"
            f"--- stderr ---\n{self.stderr}"
        )
        return self

    def assert_failed(self):
        assert self.exit_code != 0, (
            f"expected a non-zero exit for `products-cli {' '.join(self.args)}` "
            f"but it succeeded\n--- stdout ---\n{self.stdout}\n"
            f"--- stderr ---\n{self.stderr}"
        )
        return self


@pytest.fixture
def run_cli(cli_container: DockerContainer) -> Callable[..., CliResult]:
    wrapped = cli_container.get_wrapped_container()

    def _run(*args: str) -> CliResult:
        cmd = ["uv", "run", "products-cli", *args]
        exit_code, output = wrapped.exec_run(cmd, demux=True, workdir="/app")
        stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
        stdout = (stdout_b or b"").decode(errors="replace")
        stderr = (stderr_b or b"").decode(errors="replace")
        return CliResult(int(exit_code), stdout, stderr, list(args))

    return _run


def _login(
    run_cli: Callable[..., CliResult],
    base_url: str,
    username: str = DEMO_USER,
    password: str = DEMO_PASSWORD,
) -> CliResult:
    return run_cli(
        "login",
        "--base-url",
        base_url,
        "--username",
        username,
        "--password",
        password,
    )


@pytest.fixture
def cli(run_cli: Callable[..., CliResult], base_url: str) -> Callable[..., CliResult]:
    """A logged-in CLI (against the default server) ready to run `products` commands."""
    _login(run_cli, base_url).assert_ok()
    return run_cli


@pytest.fixture
def admin_cli(
    run_cli: Callable[..., CliResult], base_url: str
) -> Callable[..., CliResult]:
    """A logged-in CLI authenticated as an ADMIN user (against the default
    server), ready to run privileged `products` commands such as delete."""
    _login(run_cli, base_url, ADMIN_USER, ADMIN_PASSWORD).assert_ok()
    return run_cli


@pytest.fixture
def login(run_cli: Callable[..., CliResult]) -> Callable[[str], CliResult]:
    """Factory to log in against an arbitrary base URL (for custom-server tests)."""

    def _do(base: str) -> CliResult:
        return _login(run_cli, base)

    return _do


# --------------------------------------------------------------------------- #
# Scorecard: aggregate per-scenario results into a weighted, machine-readable
# score so grading is reproducible instead of a pile of pass/fail booleans.
#
# Each base dimension's earned points = weight * fraction, where fraction is
# either the mean of any ``score_fraction`` properties tests in that module
# recorded (graded tiers, e.g. the performance buckets), or else the share of
# its tests that passed (partial credit per dimension). Bonus dimensions add on
# top of the 100-point base, so a top submission can score above 100.
# --------------------------------------------------------------------------- #
_BASE_WEIGHTS: dict[str, tuple[str, int]] = {
    "test_login": ("Auth: login & stored base URL", 10),
    "test_products_list": ("Listing & filters", 10),
    "test_products_get_update": ("Get & update", 10),
    "test_batch_update": ("Batch update", 10),
    "test_create_delete": ("Create/delete & RBAC", 10),
    "test_refresh": ("Transparent refresh", 20),
    "test_performance": ("Batch performance (graded)", 30),
}
_BONUS_WEIGHTS: dict[str, tuple[str, int]] = {
    "test_bonus_error_hygiene": ("Error output hygiene (bonus)", 5),
    "test_bonus_network_resilience": ("Network resilience (bonus)", 5),
    "test_bonus_input_validation": ("Defensive input validation (bonus)", 5),
    "test_preemptive_refresh": ("Pre-emptive refresh (bonus)", 10),
}

_SCORECARD_PATH = REPO_ROOT / "last_scorecard.json"

# nodeid -> {"module": stem, "outcome": str | None, "props": dict}
_RESULTS: dict[str, dict] = {}


def _module_stem(nodeid: str) -> str:
    return Path(nodeid.split("::", 1)[0]).stem


def pytest_sessionstart(session: pytest.Session) -> None:
    _RESULTS.clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    stem = _module_stem(report.nodeid)
    if stem not in _BASE_WEIGHTS and stem not in _BONUS_WEIGHTS:
        return
    entry = _RESULTS.setdefault(
        report.nodeid, {"module": stem, "outcome": None, "props": {}}
    )
    if report.when == "call":
        entry["outcome"] = report.outcome
        for key, value in report.user_properties:
            entry["props"][key] = value
    elif report.when == "setup" and report.outcome in ("failed", "skipped"):
        # A setup error/skip means the test body never ran.
        if entry["outcome"] is None:
            entry["outcome"] = "error" if report.outcome == "failed" else "skipped"


def _dimension_fraction(stem: str) -> tuple[float | None, int]:
    """Return ``(fraction, n_tests)`` for a module, or ``(None, 0)`` if it did
    not run. Graded ``score_fraction`` properties take precedence over the raw
    pass rate so tiered scenarios (performance, pre-emptive refresh) count by
    quality, not just pass/fail."""
    entries = [e for e in _RESULTS.values() if e["module"] == stem]
    if not entries:
        return None, 0
    graded = [
        float(e["props"]["score_fraction"])
        for e in entries
        if "score_fraction" in e["props"]
    ]
    if graded:
        return sum(graded) / len(graded), len(entries)
    considered = [e for e in entries if e["outcome"] != "skipped"]
    if not considered:
        return None, 0
    passed = sum(1 for e in considered if e["outcome"] == "passed")
    return passed / len(considered), len(considered)


def _compute_scorecard() -> dict:
    def build(weights: dict[str, tuple[str, int]]) -> list[dict]:
        rows = []
        for stem, (label, weight) in weights.items():
            fraction, n = _dimension_fraction(stem)
            rows.append(
                {
                    "module": stem,
                    "name": label,
                    "weight": weight,
                    "ran": fraction is not None,
                    "fraction": round(fraction, 4) if fraction is not None else None,
                    "earned": round(weight * fraction, 2)
                    if fraction is not None
                    else 0.0,
                    "tests": n,
                }
            )
        return rows

    base = build(_BASE_WEIGHTS)
    bonus = build(_BONUS_WEIGHTS)
    # Only count dimensions that actually ran toward the achievable maximum, so
    # a filtered run (e.g. `-k refresh`) reports a fair partial score.
    base_max = sum(r["weight"] for r in base if r["ran"])
    base_score = sum(r["earned"] for r in base)
    bonus_score = sum(r["earned"] for r in bonus)
    partial = any(not r["ran"] for r in base)
    return {
        "base_score": round(base_score, 2),
        "base_max": base_max,
        "bonus_score": round(bonus_score, 2),
        "total": round(base_score + bonus_score, 2),
        "partial_run": partial,
        "base_dimensions": base,
        "bonus_dimensions": bonus,
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _RESULTS:
        return
    card = _compute_scorecard()
    submission = getattr(session.config, "_submission_dir", None)
    card["submission"] = str(submission) if submission is not None else None
    try:
        _SCORECARD_PATH.write_text(json.dumps(card, indent=2) + "\n")
    except OSError:  # pragma: no cover - best-effort artifact
        pass
    session.config._scorecard = card  # type: ignore[attr-defined]


def pytest_terminal_summary(
    terminalreporter, exitstatus: int, config: pytest.Config
) -> None:
    card = getattr(config, "_scorecard", None)
    if not card:
        return
    write = terminalreporter.write_line
    write("")
    write("=" * 68)
    write("SCORECARD" + (" (partial run)" if card["partial_run"] else ""))
    write("=" * 68)
    for row in card["base_dimensions"]:
        if not row["ran"]:
            write(f"  {row['name']:<34} {'—':>6}  (not run)")
            continue
        write(
            f"  {row['name']:<34} {row['earned']:>5.1f}/{row['weight']:<3} "
            f"({row['fraction'] * 100:.0f}%)"
        )
    write("-" * 68)
    write(f"  {'BASE':<34} {card['base_score']:>5.1f}/{card['base_max']}")
    for row in card["bonus_dimensions"]:
        if not row["ran"]:
            write(f"  {row['name']:<34} {'—':>6}  (not run)")
            continue
        write(
            f"  {row['name']:<34} {'+' + format(row['earned'], '.1f'):>6}/"
            f"{row['weight']} ({row['fraction'] * 100:.0f}%)"
        )
    write("=" * 68)
    write(f"  {'TOTAL':<34} {card['total']:>5.1f}")
    write(f"  (written to {_SCORECARD_PATH.name})")
    write("=" * 68)
