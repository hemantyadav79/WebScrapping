from webarchiver.core import naming


def test_url_to_html_filename_index_and_query():
    assert naming.url_to_html_filename("https://a.com/").endswith(".html")
    a = naming.url_to_html_filename("https://a.com/p?x=1")
    b = naming.url_to_html_filename("https://a.com/p?x=2")
    assert a != b  # query variants must not collide


def test_asset_filename_preserves_extension():
    assert naming.asset_filename("https://a.com/img/logo.png").endswith(".png")


def test_safe_component_blocks_traversal_and_reserved():
    assert "/" not in naming.safe_component("../../etc/passwd")
    assert naming.safe_component("con").lower().startswith("_con")
