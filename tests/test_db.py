import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import init_db


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_records_table_gets_review_columns(self):
        with tempfile.TemporaryDirectory(prefix="scrapz-migration-") as directory:
            path = Path(directory) / "research.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE records (
                   id INTEGER PRIMARY KEY,
                   job_id INTEGER NOT NULL,
                   source_url TEXT NOT NULL,
                   canonical_url TEXT NOT NULL,
                   title TEXT NOT NULL,
                   summary TEXT NOT NULL,
                   category TEXT NOT NULL,
                   published_at TEXT,
                   extraction_confidence REAL NOT NULL,
                   entities_json TEXT NOT NULL DEFAULT '[]',
                   content_hash TEXT NOT NULL,
                   collected_at TEXT NOT NULL
                )"""
            )
            connection.commit()
            connection.close()

            init_db(path)
            migrated = sqlite3.connect(path)
            columns = {row[1] for row in migrated.execute("PRAGMA table_info(records)")}
            migrated.close()

        self.assertTrue({"verification_status", "reviewed_by", "reviewed_at"} <= columns)


if __name__ == "__main__":
    unittest.main()
