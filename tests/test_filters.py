import unittest

from job_match.filters import FilterSettings, normalize, rule_filter


def job(title, location="Montreal, QC", company="Acme"):
    return {"title": title, "location": location, "company": company}


class NormalizeTest(unittest.TestCase):
    def test_strips_accents_and_case(self):
        self.assertEqual(normalize("Développeur Logiciel"), "developpeur logiciel")

    def test_handles_none(self):
        self.assertEqual(normalize(None), "")


class RuleFilterTest(unittest.TestCase):
    def setUp(self):
        self.settings = FilterSettings(
            far_locations=["mirabel"], core_locations=["montreal"], company_blacklist=["spamco"]
        )

    def check(self, title, expected, **kwargs):
        passed, reason = rule_filter(job(title, **kwargs), self.settings)
        self.assertEqual(passed, expected, reason)
        return reason

    def test_keeps_junior_developer(self):
        self.check("Junior Python Developer", True)

    def test_rejects_seniority_english_and_french(self):
        self.check("Senior Software Engineer", False)
        self.check("Sr. Data Analyst", False)
        self.check("Directrice des données", False)
        self.check("Chef d'équipe, Développement", False)

    def test_seniority_requires_word_boundary(self):
        # "lead" must not match inside other words
        self.check("Software Developer - Leadership Program", True)

    def test_rejects_non_tech_roles(self):
        self.check("Store Clerk", False)
        self.check("Infirmière clinicienne", False)

    def test_requires_a_tech_keyword(self):
        self.assertIn("no tech keyword", self.check("Warehouse Associate", False))

    def test_git_does_not_match_digital(self):
        self.check("Digital Marketing Coordinator", False)

    def test_short_and_symbol_keywords(self):
        self.check("AI Engineer", True)
        self.check("C++ Programmer", True)
        self.check("Développeur .NET", True)

    def test_internships_toggle(self):
        self.check("Software Developer Intern", True)
        self.settings.allow_internships = False
        self.check("Software Developer Intern", False)
        self.check("Stagiaire en développement logiciel", False)
        # "intern" must not match "international" / "internal"
        self.check("Internal Tools Developer", True)

    def test_far_location_rejected_unless_remote_or_allowed(self):
        self.check("Data Analyst", False, location="Mirabel, QC")
        self.check("Data Analyst (Remote)", True, location="Mirabel, QC")
        self.settings.allow_far_locations = True
        self.check("Data Analyst", True, location="Mirabel, QC")

    def test_strict_core_only(self):
        self.settings.strict_core_only = True
        self.check("Data Analyst", True, location="Montréal, QC")
        self.check("Data Analyst", False, location="Laval, QC")

    def test_company_blacklist(self):
        self.check("Junior Developer", False, company="SpamCo Staffing")


if __name__ == "__main__":
    unittest.main()
