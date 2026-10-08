"""Tests de conciliación territorial: mapping INE, tiers y persistencia."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

from collector.geo import _hav, _norm, reconcile
from collector.geo_spain import (
    CCAA_SLUGS,
    PROVINCE_SLUGS,
    PROVINCES,
    province_code,
)


class TestProvinceMapping:
    def test_all_codes_unique(self):
        names = [v[0] for v in PROVINCES.values()]
        assert len(names) == len(set(names)) == 52

    def test_renfe_spellings(self):
        assert province_code("CORUÑA, A") == "15"
        assert province_code("RIOJA, LA") == "26"
        assert province_code("VALENCIA/VALÈNCIA") == "46"
        assert province_code("ALICANTE/ALACANT") == "03"
        assert province_code("BIZKAIA") == "48"
        assert province_code("GIPUZKOA") == "20"
        assert province_code("MADRID") == "28"
        assert province_code("¿INVENTADA?") is None

    def test_ccaa_consistency(self):
        for cpro, (_p, ccode, _cn) in PROVINCES.items():
            assert len(cpro) == 2 and len(ccode) == 2

    def test_slugs(self):
        assert PROVINCE_SLUGS["guadalajara"] == "19"
        assert PROVINCE_SLUGS["a-coruna"] == "15"
        assert CCAA_SLUGS["madrid"] == "13"


class _FakeResult:
    def __init__(self, rows=()): self._r = rows
    def all(self): return list(self._r)


class _FakeConn:
    """Conn mínima: guarda los upserts y devuelve filas geo existentes."""
    def __init__(self):
        self.geo = {}
        self.select_calls = 0
        self.active_calls = 0

    def execute(self, stmt, params=None):
        sql = str(stmt).lstrip()
        if sql.startswith("SELECT feed, stop_id FROM geo_station"):
            return _FakeResult([(f, s) for (f, s) in self.geo])
        if sql.startswith("INSERT INTO geo_station"):
            self.geo[(params["feed"], params["stop_id"])] = dict(params)
            return _FakeResult()
        if sql.startswith("UPDATE geo_station SET active=0"):
            self.active_calls += 1
            self.geo[(params["f"], params["s"])]["active"] = 0
            return _FakeResult()
        return _FakeResult()


CAT = {
    "10001": {"desc": "ATOCA", "pob": "MADRID", "prov_name": "MADRID",
              "lat": 40.4066, "lon": -3.6894, "src": "renfe_completo"},
    "62000": {"desc": "BARCELONA-SANTS", "pob": "BARCELONA",
              "prov_name": "BARCELONA", "lat": 41.3795, "lon": 2.1404,
              "src": "renfe_completo"},
}


def _stop(feed, sid, name, lat=None, lon=None):
    return {"feed": feed, "stop_id": sid, "name": name, "lat": lat, "lon": lon}


class TestReconcile:
    def test_exact_code_join(self):
        conn = _FakeConn()
        stats = reconcile(conn, [_stop("cer", "10001", "Madrid-Atocha",
                                       40.4066, -3.6894)], CAT)
        g = conn.geo[("cer", "10001")]
        assert g["cpro"] == "28" and g["provincia"] == "Madrid"
        assert g["source"] == "catalogo" and stats["catalogo"] == 1

    def test_missing_coords_never_inferred(self):
        # sin coordenadas -> nunca inferir, ni por cadena
        conn = _FakeConn()
        stats = reconcile(conn, [
            _stop("cer", "10001", "Madrid-Atocha", 40.4066, -3.6894),
            _stop("cer", "99999", "Desconocida", None, None)], CAT)
        g = conn.geo[("cer", "99999")]
        assert g["cpro"] is None and g["source"] == "sin_datos"
        assert stats["sin_clasificar"] == 1

    def test_geo_inferida_neighbour(self, monkeypatch):
        # estación sin catálogo a 1 km de Atocha -> Madrid por vecino
        # (cartociudad desactivado para probar el fallback offline)
        import collector.geo as geo
        monkeypatch.setattr(geo, "_cartociudad", lambda la, lo: None)
        conn = _FakeConn()
        reconcile(conn, [
            _stop("cer", "10001", "Madrid-Atocha", 40.4066, -3.6894),
            _stop("ld", "99001", "Apeadero Vecino", 40.412, -3.685)], CAT)
        g = conn.geo[("ld", "99001")]
        assert g["cpro"] == "28" and g["source"] == "geo_inferida"

    def test_border_ambiguity_rejected(self, monkeypatch):
        # a medio camino entre provincias distintas -> ambiguo
        import collector.geo as geo
        monkeypatch.setattr(geo, "_cartociudad", lambda la, lo: None)
        conn = _FakeConn()
        reconcile(conn, [
            _stop("cer", "10001", "A", 40.0, -3.0),
            _stop("cer", "62000", "B", 40.03, -3.0),
            _stop("cer", "99002", "Frontera", 40.014, -3.0)], CAT)
        g = conn.geo[("cer", "99002")]
        assert g["cpro"] is None  # 1,7 km de cada: ambiguo, no se clasifica

    def test_same_physical_station_both_feeds(self):
        conn = _FakeConn()
        reconcile(conn, [
            _stop("cer", "10001", "Atocha", 40.4066, -3.6894),
            _stop("ld", "10001", "Atocha", 40.4066, -3.6894)], CAT)
        assert conn.geo[("cer", "10001")]["cpro"] == "28"
        assert conn.geo[("ld", "10001")]["cpro"] == "28"

    def test_disappeared_stop_deactivated_not_deleted(self):
        conn = _FakeConn()
        conn.geo[("cer", "12345")] = {"cpro": "28", "active": 1}
        reconcile(conn, [_stop("cer", "10001", "A", 40.0, -3.0)], CAT)
        assert conn.geo[("cer", "12345")]["active"] == 0
        assert conn.geo[("cer", "12345")]["cpro"] == "28"  # conservada

    def test_cartociudad_used_for_unknown(self, monkeypatch):
        import collector.geo as geo
        monkeypatch.setattr(geo, "_cartociudad",
                            lambda la, lo: {"cpro": "08", "muni": "Vic",
                                            "muni_code": "08298",
                                            "ccaa_code": "09"})
        conn = _FakeConn()
        reconcile(conn, [_stop("cer", "77109", "Vic", 41.9305, 2.2499)], CAT)
        g = conn.geo[("cer", "77109")]
        assert g["cpro"] == "08" and g["provincia"] == "Barcelona"
        assert g["source"] == "cartociudad" and g["poblacion"] == "Vic"


class TestGeoRows:
    def test_norm(self):
        assert _norm("Málaga María Zambrano") == "malaga maria zambrano"

    def test_hav(self):
        # ~111 km por grado de latitud
        assert 109_000 < _hav(40.0, -3.0, 41.0, -3.0) < 113_000
