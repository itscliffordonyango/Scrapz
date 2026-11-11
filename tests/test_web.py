import os
import re
import tempfile
import unittest

from fastapi.testclient import TestClient


_state_dir = tempfile.TemporaryDirectory(prefix="scrapz-tests-")
os.environ["SCRAPZ_STATE_DIR"] = _state_dir.name

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app, safe_csv_cell  # noqa: E402


class WebApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(_state_dir.cleanup)
        cls.settings = get_settings()
        db.init_db(cls.settings.database_path)
        cls.admin_id = db.create_user(
            cls.settings.database_path, "admin-test", "test-password-123", "admin"
        )
        cls.analyst_id = db.create_user(
            cls.settings.database_path, "analyst-test", "test-password-123", "analyst"
        )
        db.create_user(cls.settings.database_path, "viewer-test", "test-password-123", "viewer")

    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def _login(self, username):
        response = self.client.get("/login")
        token = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
        return self.client.post(
            "/login",
            data={"username": username, "password": "test-password-123", "csrf": token},
            follow_redirects=False,
        )

    def test_login_dashboard_and_static_asset(self):
        response = self._login("admin-test")
        self.assertEqual(response.status_code, 303)
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Start a job", dashboard.text)
        css = self.client.get("/static/style.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("color-scheme", css.text)

    def test_viewer_cannot_open_admin_source_registry(self):
        self._login("viewer-test")
        response = self.client.get("/sources", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/?notice=Administrator+access+required")

    def test_health_route(self):
        response = self.client.get("/health")
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_csv_cells_cannot_start_spreadsheet_formulas(self):
        self.assertEqual(safe_csv_cell("=SUM(1,1)"), "'=SUM(1,1)")
        self.assertEqual(safe_csv_cell("  =SUM(1,1)"), "'  =SUM(1,1)")
        self.assertEqual(safe_csv_cell("ordinary text"), "ordinary text")

    def test_analyst_can_record_audited_source_review(self):
        job_id = db.create_job(
            self.settings.database_path,
            self.admin_id,
            "",
            None,
            "https://example.org/review-fixture",
            "other",
        )
        db.update_job(self.settings.database_path, job_id, "completed", 0)
        db.add_records(
            self.settings.database_path,
            [
                {
                    "job_id": job_id,
                    "source_url": "https://example.org/review-fixture",
                    "canonical_url": "https://example.org/review-fixture",
                    "title": "Review fixture",
                    "summary": "Test record",
                    "category": "other",
                    "published_at": None,
                    "extraction_confidence": 0.8,
                    "entities": [],
                    "content_hash": f"review-{job_id}",
                    "collected_at": db.utc_now(),
                }
            ],
        )
        record = db.list_records(self.settings.database_path, "Review fixture")[0]
        self._login("analyst-test")
        page = self.client.get("/records")
        token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        response = self.client.post(
            f"/records/{record['id']}/review",
            data={"csrf": token, "verification_status": "source_checked"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        updated = db.record_by_id(self.settings.database_path, record["id"])
        self.assertEqual(updated["verification_status"], "source_checked")
        self.assertEqual(updated["reviewed_by"], self.analyst_id)


if __name__ == "__main__":
    unittest.main()
