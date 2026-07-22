"""Documented scenario: `login` authenticates and stores base URL + tokens."""


def test_login_prints_status_ok(run_cli, base_url):
    result = run_cli(
        "login",
        "--base-url",
        base_url,
        "--username",
        "demo",
        "--password",
        "password123",
    ).assert_ok()
    assert result.json() == {"status": "ok"}


def test_products_commands_do_not_require_base_url(cli):
    # `cli` has logged in; a subsequent products command must reuse the stored
    # base URL without --base-url being passed again.
    result = cli("products", "list").assert_ok()
    assert isinstance(result.json(), list)


def test_login_with_bad_password_fails(run_cli, base_url):
    result = run_cli(
        "login",
        "--base-url",
        base_url,
        "--username",
        "demo",
        "--password",
        "wrong-password",
    ).assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"


def test_login_requires_base_url(run_cli):
    # --base-url is a required option on `login`; omitting it must fail rather
    # than silently falling back to a previously stored base URL.
    result = run_cli(
        "login", "--username", "demo", "--password", "password123"
    ).assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"
