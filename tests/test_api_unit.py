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
