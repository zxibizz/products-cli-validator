"""Documented + hidden scenarios for `products list` and its filters.

`products list` must print a JSON *array* of products (extracted from the
server's paginated envelope) and map each flag to the right query parameter.
Seed data (15 products) is deterministic; see server/app/db.py.
"""

PRODUCT_KEYS = {"id", "name", "section", "description", "discount", "price"}


def test_list_returns_all_seeded_products_as_json_array(cli):
    products = cli("products", "list", "--limit", "200").json()
    assert isinstance(products, list), "list must print a JSON array, not the envelope"
    assert len(products) == 15
    assert PRODUCT_KEYS <= set(products[0])


def test_filter_by_section(cli):
    products = cli("products", "list", "--section", "books").json()
    assert len(products) == 3
    assert all(p["section"] == "books" for p in products)


def test_filter_by_name_substring_case_insensitive(cli):
    products = cli("products", "list", "--name", "CODE").json()
    names = {p["name"] for p in products}
    assert "Clean Code" in names


def test_filter_by_price_range(cli):
    products = cli("products", "list", "--min-price", "50", "--max-price", "100").json()
    assert products
    assert all(50 <= p["price"] <= 100 for p in products)


def test_filter_has_discount(cli):
    products = cli("products", "list", "--has-discount", "--limit", "200").json()
    assert products
    assert all(p["discount"] > 0 for p in products)


def test_filter_no_discount(cli):
    products = cli("products", "list", "--no-discount", "--limit", "200").json()
    assert products
    assert all(p["discount"] == 0 for p in products)


def test_limit_and_offset_pagination(cli):
    first = cli("products", "list", "--limit", "5", "--offset", "0").json()
    second = cli("products", "list", "--limit", "5", "--offset", "5").json()
    assert len(first) == 5
    assert len(second) == 5
    assert {p["id"] for p in first}.isdisjoint({p["id"] for p in second})


def test_combined_filters(cli):
    products = cli(
        "products", "list", "--section", "electronics", "--has-discount"
    ).json()
    assert products
    assert all(p["section"] == "electronics" and p["discount"] > 0 for p in products)


def test_inverted_price_range_is_rejected(cli):
    # Server returns HTTP 400; the CLI must surface a non-zero exit + stderr.
    result = cli(
        "products", "list", "--min-price", "100", "--max-price", "50"
    ).assert_failed()
    assert result.stderr.strip(), "expected an error message on stderr"


def test_list_no_matches_returns_empty_array(cli):
    # A filter that matches nothing is success (exit 0, empty JSON array),
    # not an error — a common bug is treating an empty result as a failure.
    result = cli("products", "list", "--name", "zzz-nonexistent-zzz").assert_ok()
    assert result.json() == []
