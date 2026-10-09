from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci.classify_frontend_changes import (
    COMPONENT,
    management_dependencies,
    needs_frontend,
    needs_mocked_browser,
)


class FrontendClassifierTests(unittest.TestCase):
    def test_current_management_contract_and_unrelated_backend(self):
        dependencies = management_dependencies(Path.cwd())
        for name in (
            "management_browser.py", "management_permissions.py",
            "management_projections.py", "management_loading_performance.py",
            "agent_config.py", "memory.py", "request_rules.py",
            "debug_management_projection.py", "model_catalog.py",
        ):
            path = (COMPONENT / name).as_posix()
            self.assertTrue(needs_frontend(path, dependencies), path)
        self.assertFalse(needs_frontend((COMPONENT / "ai_task.py").as_posix(), dependencies))
        self.assertFalse(needs_frontend("docs/README.md", dependencies))
        self.assertTrue(needs_frontend("tests_browser/new-journey.spec.mjs", dependencies))
        self.assertTrue(needs_frontend((COMPONENT / "frontend" / "management-panel.js").as_posix(), dependencies))

    def test_backend_management_selects_genuine_but_not_mocked_browser(self):
        path = (COMPONENT / "management_permissions.py").as_posix()
        self.assertTrue(needs_frontend(path, management_dependencies(Path.cwd())))
        self.assertFalse(needs_mocked_browser(path))
        for path in ("frontend/src/main.js", "tests_browser/new.spec.mjs", "ci/Dockerfile.stable"):
            self.assertTrue(needs_mocked_browser(path), path)

    def test_new_transitive_management_helper_is_classified_without_workflow_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            component = repo / COMPONENT
            component.mkdir(parents=True)
            (component / "management_ui.py").write_text("from . import new_projection\n", encoding="utf-8")
            (component / "new_projection.py").write_text("from . import deep_helper\n", encoding="utf-8")
            (component / "deep_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            with patch("ci.classify_frontend_changes.ROOTS", {"management_ui.py"}):
                dependencies = management_dependencies(repo)
            self.assertIn((COMPONENT / "deep_helper.py").as_posix(), dependencies)


if __name__ == "__main__":
    unittest.main()
