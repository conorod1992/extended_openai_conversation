"""Ensure new frontend quality checks cannot silently skip their introducing PR."""

from pathlib import Path
import unittest

from ci.classify_frontend_changes import needs_frontend


ROOT = Path(__file__).resolve().parents[1]


class FrontendQualityGateTests(unittest.TestCase):
    def test_quality_configs_trigger_frontend_pr_checks(self):
        for path in (
            "frontend/eslint.config.mjs",
            "frontend/.stylelintrc.json",
            "frontend/.htmlvalidate.json",
            ".github/workflows/frontend.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(needs_frontend(path, set()))

    def test_frontend_workflow_executes_each_validator(self):
        workflow = (ROOT / ".github/workflows/frontend.yml").read_text(encoding="utf-8")
        for expected in (
            "Lint shipped JavaScript",
            "Validate source CSS",
            "Validate static HTML fixtures",
            "frontend/eslint.config.mjs",
            "frontend/.stylelintrc.json",
            "frontend/.htmlvalidate.json",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, workflow)
        self.assertIn("pull_request:", workflow)
