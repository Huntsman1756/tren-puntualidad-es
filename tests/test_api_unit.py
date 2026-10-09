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


@pytest.fixture
def rl(monkeypatch):
    """Parsea listas de confianza de prueba y devuelve (_rl_key, req)."""
    from types import SimpleNamespace

    import api.main as main

    monkeypatch.setattr(main, "_TRUSTED", main._parse_cidrs("10.0.1.0/24"))
    monkeypatch.setattr(main, "_INTERNAL", main._parse_cidrs("10.0.5.0/24,127.0.0.1/32"))

    def req(host, xff=None):
        return SimpleNamespace(client=SimpleNamespace(host=host),
                               headers={"x-forwarded-for": xff} if xff else {})
    return main._rl_key, req


def test_rl_untrusted_peer_spoofed_xff_keyed_by_peer(rl):
    key, req = rl
    assert key(req("8.8.8.8", "1.2.3.4")) == "8.8.8.8"
    assert key(req("8.8.8.8", "9.9.9.9, 1.2.3.4")) == "8.8.8.8"


def test_rl_trusted_proxy_uses_client_from_xff(rl):
    key, req = rl
    assert key(req("10.0.1.5", "9.9.9.9, 1.2.3.4")) == "1.2.3.4"


def test_rl_trusted_proxy_chain_skips_trusted_hops(rl):
    key, req = rl
    assert key(req("10.0.1.5", "1.2.3.4, 10.0.1.9")) == "1.2.3.4"


def test_rl_internal_peer_without_xff_exempt(rl):
    key, req = rl
    assert key(req("10.0.5.7")) is None
    assert key(req("127.0.0.1")) is None


def test_rl_internal_non_proxy_with_xff_keyed_by_peer(rl):
    key, req = rl
    # interna pero no proxy de confianza: XFF ignorado, sin exención
    assert key(req("10.0.5.7", "1.2.3.4")) == "10.0.5.7"


def test_rl_garbage_xff_falls_back_to_peer(rl):
    key, req = rl
    assert key(req("10.0.1.5", "foo, 10.0.1.9, bar")) == "10.0.1.5"
    assert key(req("10.0.1.5", "not-an-ip")) == "10.0.1.5"


def test_rl_unparsable_peer_is_plain_key(rl):
    key, req = rl
    assert key(req("unix-socket")) == "unix-socket"
