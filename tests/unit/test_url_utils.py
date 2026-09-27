from webarchiver.core import url_utils as u


def test_normalize_strips_fragment_default_port_and_sorts_query():
    got = u.normalize_url("HTTP://Example.com:80/a//b/?b=2&a=1#frag")
    assert got == "http://example.com/a/b/?a=1&b=2"


def test_normalize_drops_tracking_params():
    got = u.normalize_url("https://x.com/p?utm_source=g&id=5")
    assert got == "https://x.com/p?id=5"


def test_is_valid_http_url():
    assert u.is_valid_http_url("https://a.com")
    assert not u.is_valid_http_url("ftp://a.com")
    assert not u.is_valid_http_url("not a url")


def test_resolve_url_rejects_non_http_schemes():
    assert u.resolve_url("https://a.com/", "mailto:x@y.com") is None
    assert u.resolve_url("https://a.com/", "/page") == "https://a.com/page"


def test_same_domain_handles_www_and_subdomains():
    assert u.is_same_domain("https://www.a.com/x", "a.com")
    assert not u.is_same_domain("https://b.a.com/x", "a.com")
    assert u.is_same_domain("https://b.a.com/x", "a.com", include_subdomains=True)
