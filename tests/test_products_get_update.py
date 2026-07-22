"""Documented + hidden scenarios for `products get` and `products update`."""

PRODUCT_KEYS = {"id", "name", "section", "description", "discount", "price"}


def test_get_product_by_id(cli, server):
    product = cli("products", "get", "--id", "5").json()
    assert PRODUCT_KEYS <= set(product), "get must return the full product object"
    # The reported product must match the actual database row.
    assert product == server.fetch_product(5)


def test_get_missing_product_fails(cli):
    # Server returns 404; CLI must exit non-zero with an error message.
    result = cli("products", "get", "--id", "9999").assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"


def test_update_product_fields(cli, server):
    updated = cli(
        "products", "update", "--id", "1", "--discount", "33", "--price", "19.5"
    ).json()
    assert updated["id"] == 1
    assert updated["discount"] == 33
    assert updated["price"] == 19.5

    # The change must be persisted in the database.
    row = server.fetch_product(1)
    assert row["discount"] == 33
    assert row["price"] == 19.5


def test_update_only_changes_given_fields(cli, server):
    # PATCH semantics: updating one field must leave the others untouched
    # (a PUT-style write that resends every field would null them out).
    original = server.fetch_product(2)

    cli("products", "update", "--id", "2", "--section", "peripherals").assert_ok()

    row = server.fetch_product(2)
    assert row["section"] == "peripherals"
    for key in PRODUCT_KEYS - {"section"}:
        assert row[key] == original[key], f"{key} must be unchanged by the update"


def test_update_invalid_discount_fails(cli):
    # discount is constrained to 0..100 server-side (HTTP 422); the CLI must
    # surface that as a non-zero exit with an error message.
    result = cli("products", "update", "--id", "1", "--discount", "150").assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"


def test_update_missing_product_fails(cli):
    result = cli(
        "products", "update", "--id", "9999", "--discount", "10"
    ).assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"
