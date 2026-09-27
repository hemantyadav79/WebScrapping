from webarchiver.config.models import GlobalConfig
from webarchiver.proxy.manager import ProxyManager


def _cfg(**kw):
    return GlobalConfig(**kw)


def test_disabled_when_no_provider():
    pm = ProxyManager(_cfg())
    assert not pm.enabled
    assert pm.current() is None


def test_direct_proxy_takes_precedence():
    pm = ProxyManager(_cfg(proxy="http://1.2.3.4:8080", proxy_provider="brightdata", proxy_host="x"))
    assert pm.enabled
    assert pm.current() == "http://1.2.3.4:8080"


def test_brightdata_username_format_and_sticky():
    pm = ProxyManager(_cfg(
        proxy_provider="brightdata", proxy_host="brd.superproxy.io", proxy_port=22225,
        proxy_username="brd-customer-c1-zone-z1", proxy_password="pw",
        proxy_country="au", proxy_session_type="sticky",
    ))
    url1 = pm.current()
    url2 = pm.current()
    assert url1 == url2  # sticky reuses the session
    assert "brd-customer-c1-zone-z1-country-au-session-" in url1
    assert "@brd.superproxy.io:22225" in url1


def test_rotating_changes_each_call():
    pm = ProxyManager(_cfg(
        proxy_provider="oxylabs", proxy_host="pr.oxylabs.io", proxy_port=7777,
        proxy_username="customer-c1", proxy_password="pw", proxy_session_type="rotating",
    ))
    a, b = pm.current(), pm.current()
    assert a != b
    assert "customer-c1-cc-au-sessid-" in a


def test_failover_quarantines_sticky_session():
    pm = ProxyManager(_cfg(
        proxy_provider="smartproxy", proxy_host="gate.smartproxy.com", proxy_port=7000,
        proxy_username="user-c1", proxy_password="pw", proxy_max_failures=2,
    ))
    first = pm.current()
    pm.report_failure(blocked=True)
    pm.report_failure(blocked=True)  # hits max_failures -> next current() rotates
    second = pm.current()
    assert first != second
    health = pm.get_health_report()
    assert health["blocked_requests"] == 2
