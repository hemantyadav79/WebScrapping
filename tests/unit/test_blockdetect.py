from webarchiver.crawl.blockdetect import is_blocked


def test_block_status_codes():
    assert is_blocked("<html>ok</html>", 403)[0]
    assert is_blocked("<html>ok</html>", 429)[0]


def test_challenge_markers():
    assert is_blocked("<html>Please enable JavaScript and cookies to continue", 200)[0]
    assert is_blocked("<html>Just a moment...</html>", 200)[0]
    assert is_blocked("window.KPSDK.configure(...)", 200)[0]


def test_tiny_body_flagged():
    blocked, reason = is_blocked("<html></html>", 200, min_size_bytes=2048)
    assert blocked and "small" in reason


def test_real_page_passes():
    html = "<html><head><title>Listings</title></head><body>" + "x" * 5000 + "</body></html>"
    assert not is_blocked(html, 200)[0]


def test_real_page_with_embedded_kasada_script_not_blocked():
    # Protected sites embed the Kasada (kpsdk) script on EVERY page; a real page
    # that also has __NEXT_DATA__ / cards must not be misclassified as a block.
    html = (
        '<html><head><script id="__NEXT_DATA__">{}</script>'
        '<script src="https://x/kpsdk.js"></script></head><body>'
        + '<div class="residential-card">x</div>' * 40
        + "</body></html>"
    )
    assert not is_blocked(html, 200)[0]
