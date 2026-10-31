import unittest
from unittest import mock

from job_match import db
from job_match.descriptions import classify, fetch_all, parse_description
from job_match.throttle import Blocked, Throttle

DETAIL_HTML = """
<section><div class="show-more-less-html__markup">
  <p>We are hiring.</p><ul><li>Python</li><li>SQL</li></ul>
</div></section>
"""


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class ClassifyTest(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(classify("text", 200), "pending_ai")
        self.assertEqual(classify(None, 200), "description_missing")
        self.assertEqual(classify("  ", 200), "description_empty")
        self.assertEqual(classify(None, 404), "description_failed_404")
        self.assertEqual(classify(None, 500), "description_failed_status_error")
        self.assertEqual(classify(None, None), "request_exception")


class ParseDescriptionTest(unittest.TestCase):
    def test_extracts_text_with_line_breaks(self):
        self.assertEqual(parse_description(DETAIL_HTML), "We are hiring.\nPython\nSQL")

    def test_missing_markup(self):
        self.assertIsNone(parse_description("<html></html>"))


class FetchAllTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.conn.executemany(
            "INSERT INTO jobs (job_id, title, status, posted_iso) VALUES (?, ?, 'pending_desc', ?)",
            [("1", "A", "2026-10-02"), ("2", "B", "2026-10-01"), ("old", "C", "2026-01-01")],
        )
        self.throttle = Throttle(sleep=lambda s: None)

    def status(self, job_id):
        return self.conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0]

    def test_updates_each_job_and_respects_posted_since(self):
        session = mock.Mock()
        session.get.side_effect = [FakeResponse(200, DETAIL_HTML), FakeResponse(404)]

        ready = fetch_all(self.conn, "2026-09-30", self.throttle, session=session)

        self.assertEqual(ready, 1)
        self.assertEqual(self.status("1"), "pending_ai")
        self.assertEqual(self.status("2"), "description_failed_404")
        self.assertEqual(self.status("old"), "pending_desc")

    def test_retries_after_rate_limit(self):
        session = mock.Mock()
        session.get.side_effect = [FakeResponse(429), FakeResponse(200, DETAIL_HTML), FakeResponse(404)]

        fetch_all(self.conn, "2026-09-30", self.throttle, session=session)

        self.assertEqual(self.status("1"), "pending_ai")
        self.assertEqual(self.throttle.total_rate_limits, 1)

    def test_stops_when_blocked(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse(999)
        with self.assertRaises(Blocked):
            fetch_all(self.conn, "2026-09-30", self.throttle, session=session)
        self.assertEqual(self.status("1"), "pending_desc")


if __name__ == "__main__":
    unittest.main()
