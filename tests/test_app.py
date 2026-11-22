import os
import tempfile
import unittest

from app import create_app
from models import db, Rule

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.tmp.name}",
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        self.old_password = os.environ.get("ADMIN_PASSWORD")
        os.environ["ADMIN_PASSWORD"] = "test-password"

    def tearDown(self):
        if self.old_password is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self.old_password
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(self.tmp.name)

    def login(self):
        return self.client.post("/login", data={"password": "test-password"}, follow_redirects=True)

    def test_health(self):
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_login_required(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_add_rule(self):
        self.login()
        response = self.client.post("/rules", data={"keyword": "авария", "target": "TG", "recipient_id": "123"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(Rule.query.count(), 1)

if __name__ == "__main__":
    unittest.main()
