import time
import unittest
from datetime import date

from job_match.config import ConfigError, resolve_posted_since, section
from job_match.llm_gateway import OverallTimeout, call_with_deadline
from job_match.prompts import CandidateProfile, build_system_prompt, build_user_prompt
from job_match.scraper import ScraperSettings


class ConfigTest(unittest.TestCase):
    def test_explicit_posted_since(self):
        self.assertEqual(resolve_posted_since({"posted_since": "2026-09-30"}), "2026-09-30")

    def test_lookback_days(self):
        general = {"posted_since": "", "lookback_days": 10}
        self.assertEqual(resolve_posted_since(general, today=date(2026, 10, 31)), "2026-10-21")

    def test_invalid_date(self):
        with self.assertRaises(ConfigError):
            resolve_posted_since({"posted_since": "30/09/2026"})

    def test_section_rejects_unknown_keys(self):
        self.assertEqual(section(ScraperSettings, {"max_pages_per_search": 5}).max_pages_per_search, 5)
        with self.assertRaises(ConfigError):
            section(ScraperSettings, {"max_pagez": 5})


class PromptsTest(unittest.TestCase):
    def test_system_prompt_uses_profile(self):
        prompt = build_system_prompt(
            CandidateProfile(market="data roles in Toronto", target_roles=["Data Analyst", "BI Developer"],
                             standout_skills="dbt experience")
        )
        self.assertIn("data roles in Toronto", prompt)
        self.assertIn("Data Analyst, BI Developer", prompt)
        self.assertIn("All must-haves + dbt experience", prompt)

    def test_user_prompt_contains_job_and_resume(self):
        job = {"title": "Dev", "company": "Acme", "location": "Remote", "description": "Python"}
        prompt = build_user_prompt(job, "  my resume  ")
        for text in ("resume: my resume\n", "Title: Dev", "Company: Acme", "Description: Python"):
            self.assertIn(text, prompt)


class DeadlineTest(unittest.TestCase):
    def test_returns_value(self):
        self.assertEqual(call_with_deadline(lambda: 42, 1), 42)

    def test_reraises_errors(self):
        def boom():
            raise ValueError("x")

        with self.assertRaises(ValueError):
            call_with_deadline(boom, 1)

    def test_times_out(self):
        with self.assertRaises(OverallTimeout):
            call_with_deadline(lambda: time.sleep(2), 0.1)


if __name__ == "__main__":
    unittest.main()
