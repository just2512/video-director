from tools.http_connect_socks_proxy import parse_connect_target, parse_socks_upstream


def test_socks5h_upstream_keeps_remote_dns_and_auth_without_logging_url():
    upstream = parse_socks_upstream({
        "ALL_PROXY": "socks5h://user:pass@127.0.0.1:1080",
        "HTTPS_PROXY": "https://fallback.invalid",
    })
    assert upstream == {
        "scheme": "socks5h",
        "host": "127.0.0.1",
        "port": 1080,
        "rdns": True,
        "username": "user",
        "password": "pass",
    }


def test_non_socks_proxy_is_not_replaced():
    assert parse_socks_upstream({
        "ALL_PROXY": "https://proxy.example:8443",
        "HTTPS_PROXY": "http://proxy.example:3128",
    }) is None


def test_connect_authority_parses_dns_and_ipv6():
    assert parse_connect_target("github.com:443") == ("github.com", 443)
    assert parse_connect_target("[2001:db8::1]:443") == ("2001:db8::1", 443)


def test_connect_authority_rejects_invalid_ports_and_hosts():
    for value in ("github.com", "github.com:0", "github.com:65536", "[::1]443"):
        try:
            parse_connect_target(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid CONNECT authority: {value}")
