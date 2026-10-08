import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collector"))
sys.path.insert(0, os.path.join(ROOT, "api"))

# La BD de test es aislada: TEST_DATABASE_URL o el postgres de CI/dev.
# Debe fijarse ANTES de importar collector.db / api.db (crean el engine).
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL",
                   "postgresql+psycopg://renfe:renfe@localhost:5432/renfe_test"))
