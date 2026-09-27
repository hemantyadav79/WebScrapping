from webarchiver.archive.link_rewriter import LinkRewriter


def test_rewrites_internal_links_and_assets():
    pages = {"https://a.com/about": "html/about.html", "https://a.com/": "html/index.html"}
    assets = {"https://a.com/img/logo.png": "images/png/logo.png"}
    rw = LinkRewriter(pages, assets)
    html = '<a href="/about">x</a><img src="/img/logo.png">'
    out = rw.rewrite(html, base_url="https://a.com/", page_relpath="html/index.html")
    assert 'href="about.html"' in out
    assert '../images/png/logo.png' in out


def test_leaves_external_untouched():
    rw = LinkRewriter({}, {})
    out = rw.rewrite('<a href="https://ext.com/x">e</a>', base_url="https://a.com/",
                     page_relpath="html/index.html")
    assert "https://ext.com/x" in out
