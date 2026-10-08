"""Protect immutable acceptance baselines and Windows temporary-file cleanup."""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path


def test_shutdown_backup_is_immutable_and_releases_sqlite_handles(tmp_path):
    path = Path(__file__).resolve().parents[2] / "evals" / "run_online.py"
    spec = importlib.util.spec_from_file_location("stockpilot_online_eval", path)
    online = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(online)
    source, frozen = tmp_path / "source.db", tmp_path / "frozen.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('original')")
        connection.commit()
    online.copy_shutdown_database(source, frozen)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE sample SET value='later write'")
        connection.commit()
    with closing(sqlite3.connect(frozen)) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "original"
    # On Windows these fail if the backup helper leaves either handle open.
    source.unlink()
    frozen.unlink()
