"""Required scenarios for `products create` / `products delete`.

Both commands are part of the required CLI contract. `delete` is an ADMIN-only
action server-side, so the happy-path round-trip runs as admin, and a separate
test asserts a non-admin user is refused.
"""


def test_create_then_delete_product(admin_cli, server):
    # Deleting is an ADMIN-only action, so drive the whole round-trip as admin.
    created = admin_cli(
        "products",
        "create",
        "--name",
        "Test Widget",
        "--section",
        "misc",
        "--price",
        "9.99",
    ).assert_ok()
    product = created.json()
    new_id = product["id"]
    assert product["name"] == "Test Widget"

    # Verify the row was actually written to the server's database (not just
    # echoed back by the CLI).
    row = server.fetch_product(new_id)
    assert row is not None, "product was not persisted to the database"
    assert row["name"] == "Test Widget"
    assert row["section"] == "misc"
    assert row["price"] == 9.99

    # `delete` must succeed (exit 0). The assignment doesn't pin an exact ack
    # payload, so don't assert its output shape — check the database instead.
    admin_cli("products", "delete", "--id", str(new_id)).assert_ok()

    # The row must actually be gone from the database.
    assert server.fetch_product(new_id) is None, (
        "product still present in the database after delete"
    )


def test_create_applies_field_defaults(cli, server):
    # Omitting --description and --discount must fall back to the server
    # defaults ("" and 0). Verify against what the database actually stored.
    created = cli(
        "products", "create", "--name", "Bare Item", "--section", "misc", "--price", "5"
    ).assert_ok().json()

    row = server.fetch_product(created["id"])
    assert row is not None, "product was not persisted to the database"
    assert row["description"] == ""
    assert row["discount"] == 0


def test_create_invalid_price_fails(cli):
    # price must be >= 0 (HTTP 422); the CLI must surface a non-zero exit + stderr.
    result = cli(
        "products", "create", "--name", "Bad", "--section", "misc", "--price", "-5"
    ).assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"


def test_delete_requires_admin_role(cli, server):
    """Deleting is restricted to ADMIN users; the demo `demo` (a regular
    USER) must be refused. The server returns HTTP 403 and the CLI must surface
    it as a non-zero exit with an error message — not a silent success."""
    # `cli` is logged in as the non-admin demo user.
    result = cli("products", "delete", "--id", "1").assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"

    # The forbidden delete must not have touched the database.
    assert server.fetch_product(1) is not None, (
        "a forbidden delete removed the product from the database"
    )
