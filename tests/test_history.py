"""Tests del modelo histórico: decisión de día de servicio (pura).

La resolución completa contra BD está en test_collector_db.py
(integración). Aquí se cubre la regla de decisión: un candidato gana
solo si queda dentro de la tolerancia y a suficiente margen del
segundo — si no, no se infiere fecha (None)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

from collector.realtime import _pick_day

D1 = date(2026, 10, 8)
D0 = date(2026, 10, 7)
TOL, MARGIN = 4 * 3600, 90 * 60


class TestPickDay:
    def test_single_candidate_within_tol(self):
        assert _pick_day([(300, D1)], TOL, MARGIN) == "2026-10-08"

    def test_no_candidate(self):
        assert _pick_day([], TOL, MARGIN) is None

    def test_beyond_tol(self):
        assert _pick_day([(5 * 3600, D1)], TOL, MARGIN) is None

    def test_clear_winner(self):
        # servicio post-medianoche: el evento real cae al día siguiente,
        # pero el día de servicio es el del origen (err 300 vs 86100)
        assert _pick_day([(300, D0), (86100, D1)], TOL, MARGIN) == "2026-10-07"

    def test_ambiguous_midpoint_is_none(self):
        # trips diarios equidistantes: no se infiere fecha
        assert _pick_day([(43200, D0), (43200, D1)], TOL, MARGIN) is None

    def test_ambiguous_under_margin(self):
        # segundo candidato a menos de MARGIN del primero -> None
        assert _pick_day([(1000, D0), (1000 + MARGIN - 60, D1)],
                         TOL, MARGIN) is None

    def test_winner_over_margin(self):
        assert _pick_day([(1000, D0), (1000 + MARGIN, D1)],
                         TOL, MARGIN) == "2026-10-07"

    def test_best_is_sorted_not_input_order(self):
        assert _pick_day([(86100, D1), (300, D0)], TOL, MARGIN) == "2026-10-07"
