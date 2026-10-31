import sqlite3
import unittest

from job_match import db


class InitDbTest(unittest.TestCase):
    def columns(self, conn):
        return [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]

    def test_creates_full_schema(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        self.assertEqual(self.columns(conn), list(db.COLUMNS))

    def test_is_idempotent(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        db.init_db(conn)
        self.assertEqual(self.columns(conn), list(db.COLUMNS))

    def test_migrates_legacy_table(self):
        conn = db.connect(":memory:")
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO jobs VALUES ('1', 'Dev')")
        db.init_db(conn)

        self.assertEqual(set(self.columns(conn)), set(db.COLUMNS))
        row = conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual((row["title"], row["status"]), ("Dev", "new"))

    def test_human_status_is_constrained(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        conn.execute("INSERT INTO jobs (job_id, human_status) VALUES ('1', 'Applied')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO jobs (job_id, human_status) VALUES ('2', 'Maybe')")

    def test_status_counts(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        conn.executemany(
            "INSERT INTO jobs (job_id, status) VALUES (?, ?)",
            [("1", "new"), ("2", "new"), ("3", "pending_ai")],
        )
        self.assertEqual(db.status_counts(conn), {"new": 2, "pending_ai": 1})


if __name__ == "__main__":
    unittest.main()
