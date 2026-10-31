import unittest
from unittest import mock

from bs4 import BeautifulSoup

from job_match import db
from job_match.scraper import ScraperSettings, Search, parse_card, save_jobs, scrape_all
from job_match.throttle import Throttle

CARD_HTML = """
<div class="base-card" data-entity-urn="urn:li:jobPosting:4012345678">
  <a class="base-card__full-link" href="https://ca.linkedin.com/jobs/view/junior-dev-4012345678?refId=abc"></a>
  <h3> Junior Python Developer </h3>
  <h4> Acme Corp </h4>
  <span class="job-search-card__location"> Montreal, Quebec, Canada </span>
  <time datetime="2026-10-01">2 days ago</time>
</div>
"""


def card(html=CARD_HTML):
    return BeautifulSoup(html, "html.parser").find("div")


class ParseCardTest(unittest.TestCase):
    def test_extracts_fields(self):
        job = parse_card(card(), scraped_at="2026-10-03T10:00:00")
        self.assertEqual(
            job,
            {
                "job_id": "4012345678",
                "title": "Junior Python Developer",
                "company": "Acme Corp",
                "location": "Montreal, Quebec, Canada",
                "link": "https://ca.linkedin.com/jobs/view/junior-dev-4012345678",
                "posted_iso": "2026-10-01",
                "scraped_at": "2026-10-03T10:00:00",
            },
        )

    def test_card_without_id_is_skipped(self):
        self.assertIsNone(parse_card(card('<div class="base-card"><h3>x</h3></div>')))


class SearchTest(unittest.TestCase):
    def test_params_use_linkedin_names(self):
        s = Search("n", "Python", "Montreal", experience="2", posted_within="r86400", job_type="F")
        self.assertEqual(
            s.params(20),
            {"keywords": "Python", "location": "Montreal", "f_E": "2",
             "f_TPR": "r86400", "f_JT": "F", "start": 20},
        )


class SaveJobsTest(unittest.TestCase):
    def test_upsert_keeps_first_seen_and_updates_last_seen(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        job = parse_card(card(), scraped_at="2026-10-01T00:00:00")
        save_jobs(conn, [job], "search A")
        save_jobs(conn, [{**job, "scraped_at": "2026-10-02T00:00:00"}], "search B")

        row = conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["first_seen_at"], "2026-10-01T00:00:00")
        self.assertEqual(row["last_seen_at"], "2026-10-02T00:00:00")
        self.assertEqual(row["source_search"], "search A")
        self.assertEqual(row["status"], "new")


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class ScrapeAllTest(unittest.TestCase):
    def test_paginates_until_empty_page(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        session = mock.Mock()
        session.get.side_effect = [FakeResponse(200, CARD_HTML), FakeResponse(200, "")]
        throttle = Throttle(sleep=lambda s: None)

        new = scrape_all(conn, [Search("s", "k", "l")], ScraperSettings(), throttle, session)

        self.assertEqual(new, 1)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["start"], 10)

    def test_retries_server_errors_then_gives_up(self):
        conn = db.connect(":memory:")
        db.init_db(conn)
        session = mock.Mock()
        session.get.return_value = FakeResponse(503)
        sleeps = []

        new = scrape_all(
            conn, [Search("s", "k", "l")], ScraperSettings(max_retries_per_page=3),
            Throttle(sleep=sleeps.append), session,
        )

        self.assertEqual(new, 0)
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(sleeps, [60, 120])  # linear backoff between retries


if __name__ == "__main__":
    unittest.main()
