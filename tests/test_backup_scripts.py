"""Pruebas estáticas de los scripts y del servicio backup de compose (sin docker).

Comprueban estructura, invariantes y sintaxis. La prueba real de restauración
se hace con scripts/backup_verify.sh en el host.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts" / "backup_loop.sh"
VERIFY = ROOT / "scripts" / "backup_verify.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"
PROD_COMPOSE = ROOT / "infra" / "compose" / "docker-compose.prod.yml"

GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_bash():
    if GIT_BASH.exists():
        return str(GIT_BASH)
    # which() puede devolver el bash de WSL (System32); se acepta solo si existe.
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    return None


def _backup_block() -> str:
    """Texto del servicio backup dentro de docker-compose.prod.yml."""
    text = _read(PROD_COMPOSE)
    m = re.search(r"^  backup:\n(.*?)(?=^\S|^  \S)", text, re.S | re.M)
    assert m, "no se encuentra el servicio backup en docker-compose.prod.yml"
    return m.group(1)


# --- existencia y formato ----------------------------------------------------

@pytest.mark.parametrize("path", [LOOP, VERIFY, DEPLOY])
def test_script_exists(path):
    assert path.is_file(), f"falta {path}"


@pytest.mark.parametrize("path", [LOOP, VERIFY, DEPLOY])
def test_script_lf_endings(path):
    # .gitattributes: *.sh text eol=lf
    assert b"\r\n" not in path.read_bytes(), f"{path.name} tiene finales CRLF"


def test_verify_shebang_is_bash():
    first = _read(VERIFY).splitlines()[0]
    assert first.startswith("#!") and "bash" in first


def test_deploy_shebang_is_bash():
    first = _read(DEPLOY).splitlines()[0]
    assert first.startswith("#!") and "bash" in first


def test_loop_shebang_is_posix_sh():
    # El bucle corre en postgres:16-alpine (busybox sh), sin bash.
    first = _read(LOOP).splitlines()[0]
    assert first in ("#!/bin/sh", "#!/usr/bin/env sh")


# --- invariantes de scripts --------------------------------------------------

def test_verify_strict_mode():
    assert "set -euo pipefail" in _read(VERIFY)


def test_loop_pipefail():
    assert "set -o pipefail" in _read(LOOP)


def test_loop_verifies_dump_before_moving():
    text = _read(LOOP)
    assert "pg_restore --list" in text
    assert "TABLE DATA public stops" in text
    assert "TABLE DATA public observations" in text
    assert "MIN_BYTES=1048576" in text
    # el movimiento atómico debe ir después de la verificación
    assert text.index("pg_restore --list") < text.index('mv "$TMP_DUMP"')


def test_loop_writes_status_and_last_ok():
    text = _read(LOOP)
    assert "backup-status.log" in text
    assert "FAILED" in text
    assert "last-ok" in text


def test_loop_schedule_and_retries():
    text = _read(LOOP)
    assert "TARGET_SECS=$((3 * 3600 + 30 * 60))" in text
    assert "Europe/Madrid" in text
    assert "MAX_RETRIES=4" in text
    assert "RETRY_SLEEP:-900" in text
    assert "MAX_AGE_SECS=$((26 * 3600))" in text


def test_loop_rotation_counts():
    text = _read(LOOP)
    assert "keep_newest 'daily-*.dump' 14" in text
    assert "keep_newest 'renfe_*.sql.gz' 5" in text
    assert "keep_newest 'predeploy-*' 5" in text
    assert "renfe-*.dump.gz" in text and "-mtime +30" in text


def test_verify_checks_and_markers():
    text = _read(VERIFY)
    assert "RESTORE OK" in text
    assert "trap cleanup EXIT" in text
    assert "--network none" in text
    assert "--tmpfs /var/lib/postgresql/data" in text
    assert ":ro" in text
    for table in ("stops", "routes", "trips", "observations"):
        assert f"from {table}" in text
    assert "5" in text  # umbral 5 % frente a la BD viva


def test_deploy_has_non_fatal_backup_age_check():
    text = _read(DEPLOY)
    assert "/backups/last-ok" in text
    assert "26" in text
    # la comprobación no debe ser fatal: no hay exit tras el WARN
    block = text[text.index("Backup automático"):]
    assert "exit 1" not in block.split("==> Pull")[0]


# --- compose -----------------------------------------------------------------

def test_compose_backup_mounts_loop_script_readonly():
    block = _backup_block()
    assert "./scripts/backup_loop.sh:/backup_loop.sh:ro" in block


def test_compose_backup_sets_tz_and_pg_vars():
    block = _backup_block()
    assert re.search(r"TZ:\s*Europe/Madrid", block)
    for var in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE"):
        assert re.search(rf"^\s+{var}:", block, re.M), var


def test_compose_backup_runs_loop():
    assert 'command: ["sh", "/backup_loop.sh"]' in _backup_block()


def test_compose_backup_has_no_dollar_escaping_leftovers():
    # el bucle ya no vive inline; no deben quedar $$ de la versión antigua
    assert "$$" not in _backup_block()


# --- sintaxis con bash (omitido si no hay bash) ------------------------------

@pytest.mark.parametrize("path", [LOOP, VERIFY, DEPLOY])
def test_bash_syntax(path):
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash no disponible")
    result = subprocess.run(
        [bash, "-n", path.as_posix()],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
