"""Documented + hidden scenarios for `products batch-update`.

There is intentionally no bulk endpoint on the server, so `batch-update` must
apply the discount to every product in the section (however the candidate chose
to implement it) and report the number updated.
"""


def test_batch_update_sets_discount_for_whole_section(cli, server):
    result = cli(
        "products", "batch-update", "--section", "books", "--discount", "50"
    ).json()
    # books has 3 seeded products.
    assert result == {"updated": 3}

    # Confirm the discount was actually written to every books row in the DB.
    books = server.fetch_products(section="books")
    assert len(books) == 3
    assert all(p["discount"] == 50 for p in books)


def test_batch_update_leaves_other_sections_untouched(cli, server):
    cli(
        "products", "batch-update", "--section", "books", "--discount", "50"
    ).assert_ok()

    # electronics must keep its original per-product discounts in the DB.
    electronics = server.fetch_products(section="electronics")
    discounts = sorted(p["discount"] for p in electronics)
    assert discounts == [0, 0, 10, 15]


def test_batch_update_reports_zero_for_empty_section(cli, server):
    result = cli(
        "products", "batch-update", "--section", "does-not-exist", "--discount", "10"
    )
    result.assert_ok()
    assert result.json() == {"updated": 0}

    # Nothing should have been created for a section that doesn't exist.
    assert server.fetch_products(section="does-not-exist") == []
