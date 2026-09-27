from webarchiver.extract.page_parser import PageParser

HTML = """<html lang="en"><head><title>T</title>
<meta name="description" content="d"><meta name="keywords" content="k">
<link rel="canonical" href="/c"><meta property="og:title" content="OG">
<script type="application/ld+json">{"@type":"Org"}</script></head>
<body><h1>H1</h1>
<a href="/in">in</a><a href="https://ext.com/x">ext</a><a href="/d.pdf">doc</a>
<img src="/i.png"></body></html>"""


def test_extracts_all_fields():
    r = PageParser(root_host="a.com").parse(HTML, "https://a.com/")
    assert r.title == "T"
    assert r.meta_description == "d"
    assert r.meta_keywords == "k"
    assert r.canonical_url == "https://a.com/c"
    assert r.language == "en"
    assert r.open_graph["og:title"] == "OG"
    assert r.structured_data and r.structured_data[0]["@type"] == "Org"
    assert "https://a.com/in" in r.internal_links
    assert "https://ext.com/x" in r.external_links
    urls = {a.url for a in r.assets}
    assert "https://a.com/i.png" in urls
    assert "https://a.com/d.pdf" in urls
