from webarchiver.crawl.frontier import Frontier


def test_dedup_and_visited():
    f = Frontier()
    assert f.add("https://a.com/1", 0)
    assert not f.add("https://a.com/1", 0)
    task = f.pop()
    assert task.url == "https://a.com/1"
    assert f.has_visited("https://a.com/1")
    assert not f.add("https://a.com/1", 0)  # already visited


def test_serialisation_roundtrip():
    f = Frontier()
    f.add("https://a.com/1", 0)
    f.pop()
    f.add("https://a.com/2", 1)
    restored = Frontier.from_dict(f.to_dict())
    assert restored.has_visited("https://a.com/1")
    assert len(restored) == 1
