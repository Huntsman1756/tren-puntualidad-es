"""Tests unitarios de utilidades de la API (sin BD)."""
import pytest
from api.main import _norm, _parse_stops_param


def test_norm_accents():
    assert _norm("Madrid-Chamartín") == "madrid-chamartin"
    assert _norm("Vicálvaro") == "vicalvaro"
    assert _norm("San Xoán") == "san xoan"


def test_parse_stops_single():
    assert _parse_stops_param("cer:18000") == [("cer", "18000")]


def test_parse_stops_multi():
    assert _parse_stops_param("cer:18000,ld:18000") == [("cer", "18000"), ("ld", "18000")]


def test_parse_stops_bare_expands_both_feeds():
    assert _parse_stops_param("18000") == [("cer", "18000"), ("ld", "18000")]


def test_parse_stops_bad_feed():
    with pytest.raises(Exception):
        _parse_stops_param("xx:18000")


def test_parse_stops_empty():
    with pytest.raises(Exception):
        _parse_stops_param("")


def test_rate_limit_key_uses_proxy_ip_and_exempts_internal_ssr():
    from types import SimpleNamespace

    from api.main import _rl_key

    def req(host, xff=None):
        return SimpleNamespace(client=SimpleNamespace(host=host),
                               headers={"x-forwarded-for": xff} if xff else {})
    assert _rl_key(req("172.18.0.5")) is None              # SSR interno
    assert _rl_key(req("127.0.0.1")) is None
    assert _rl_key(req("172.18.0.2", "1.2.3.4")) == "1.2.3.4"
    # el cliente no puede elegir su clave anteponiendo IPs falsas
    assert _rl_key(req("172.18.0.2", "9.9.9.9, 1.2.3.4")) == "1.2.3.4"
    assert _rl_key(req("8.8.8.8")) == "8.8.8.8"
