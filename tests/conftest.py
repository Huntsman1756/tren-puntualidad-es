import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collector"))
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# BD de test AISLADA: TEST_DATABASE_URL (o el postgres de CI/dev). Debe
# fijarse ANTES de importar collector.db / api.db (crean el engine).
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://renfe:renfe@localhost:5432/renfe_test")

# las estadísticas v0.4 se montan solo con STATS_PUBLIC=1; los tests las cubren
os.environ.setdefault("STATS_PUBLIC", "1")

from dbfix import client, db_engine, scenario  # noqa: E402,F401
