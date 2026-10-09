"""Pruebas estáticas de la configuración WAHA (avisos oficiales por WhatsApp).

Sin docker ni red: comprueban compose, .env.example, scripts/aviso.sh y la guía.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROD_COMPOSE = ROOT / "infra" / "compose" / "docker-compose.prod.yml"
# el servicio WAHA vive en su propio compose para que WAHA_API_KEY:? solo
# se evalúe cuando el archivo se incluye explícitamente
WAHA_COMPOSE = ROOT / "infra" / "compose" / "docker-compose.waha.yml"
ENV_EXAMPLE = ROOT / ".env.example"
AVISO = ROOT / "scripts" / "aviso.sh"
DOC = ROOT / "docs" / "whatsapp-waha.md"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _waha_service() -> dict:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_read(WAHA_COMPOSE))
    return data["services"]["waha"]


def test_compose_waha_service_is_profiled():
    svc = _waha_service()
    assert svc["profiles"] == ["waha"]


def test_prod_compose_has_no_waha():
    """WAHA quedó aislado en docker-compose.waha.yml: si vuelve al archivo
    prod, WAHA_API_KEY:? rompería el parse de todo el stack (deploy incluido)."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_read(PROD_COMPOSE))
    assert "waha" not in data["services"]
    assert "waha_sessions" not in (data.get("volumes") or {})


def test_compose_waha_has_no_ports_nor_traefik_labels():
    svc = _waha_service()
    assert "ports" not in svc
    labels = svc.get("labels") or {}
    if isinstance(labels, list):
        labels = dict(item.split("=", 1) for item in labels)
    assert not [k for k in labels if str(k).startswith("traefik")]


def test_compose_waha_declares_volume_and_defaults():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(_read(WAHA_COMPOSE))
    svc = data["services"]["waha"]
    assert "waha_sessions" in (data.get("volumes") or {})
    assert "waha_sessions:/app/.sessions" in svc["volumes"]
    assert svc["environment"]["WHATSAPP_DEFAULT_ENGINE"] == "GOWS"
    assert str(svc["environment"]["WAHA_DASHBOARD_ENABLED"]).lower() == "false"


def test_env_example_documents_admin_and_waha():
    text = _read(ENV_EXAMPLE)
    assert "ADMIN_TOKEN" in text
    assert "WAHA_URL" in text


def test_aviso_script_is_safe():
    text = _read(AVISO)
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert "json.dumps" in text
    assert "\r" not in text


def test_aviso_script_has_valid_syntax():
    bash = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")
    if not bash or "System32" in bash:
        pytest.skip("bash no disponible")
    res = subprocess.run([bash, "-n", str(AVISO)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def test_whatsapp_doc_mentions_test_account():
    assert DOC.exists()
    text = _read(DOC)
    assert re.search(r"cuenta de pruebas", text, re.IGNORECASE)
