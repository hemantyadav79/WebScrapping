from webarchiver.extract.models import PageRecord
from webarchiver.report.sitemap import write_sitemap_json, write_sitemap_xml


def _pages():
    return [
        PageRecord(url="https://a.com/", final_url="https://a.com/", status_code=200, title="H"),
        PageRecord(url="https://a.com/about", final_url="https://a.com/about", status_code=200),
        PageRecord(url="https://a.com/bad", status_code=404, error="HTTP 404"),
    ]


def test_xml_excludes_failed_pages(tmp_path):
    p = write_sitemap_xml(_pages(), tmp_path / "sitemap.xml")
    text = p.read_text()
    assert "https://a.com/about" in text
    assert "https://a.com/bad" not in text


def test_json_hierarchy(tmp_path):
    import json
    p = write_sitemap_json(_pages(), tmp_path / "sitemap.json")
    tree = json.loads(p.read_text())
    assert tree["name"] == "/"
    assert any(c["name"] == "about" for c in tree["children"])
