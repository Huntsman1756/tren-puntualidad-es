import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collector"))
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# BD de test AISLADA: TEST_DATABASE_URL (o el postgres de CI). Debe fijarse
# antes de importar collector.db / api.db, que crean el engine al importar.
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from dbfix import client, db_engine, scenario  # noqa: E402,F401
