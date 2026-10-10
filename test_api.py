import io
import os
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from pypdf import PdfReader

from app import certificate, create_app

GOOD = [
    {"name": "Alice Johnson", "email": "alice@example.com"},
    {"name": "José Álvarez", "email": "jose@example.com"},
]


def payload(recipients=None, **extra):
    data = {"event_name": "Python Bootcamp 2026", "issuer": "Acme Academy",
            "issue_date": "2026-10-01", "recipients": GOOD if recipients is None else recipients}
    data.update(extra)
    return data


def pdf_text(data):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


class BaseTest(unittest.TestCase):
    sync = True
    overrides = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = {"DATABASE_PATH": os.path.join(self.tmp.name, "t.db"),
               "CERTIFICATE_DIR": os.path.join(self.tmp.name, "certs"),
               "SYNC_PROCESSING": self.sync, "TESTING": True}
        cfg.update(self.overrides)
        self.app = create_app(cfg)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["executor"].shutdown(wait=True)
        self.tmp.cleanup()

    def create(self, body=None):
        return self.client.post("/api/jobs", json=payload() if body is None else body)

    def wait_for(self, job_id, predicate, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").get_json()
            if predicate(job):
                return job
            time.sleep(0.02)
        self.fail("timed out waiting for job state")


class CreateJobTests(BaseTest):
    def test_create_job_returns_accepted_with_location(self):
        res = self.create()
        self.assertEqual(res.status_code, 202)
        body = res.get_json()
        self.assertEqual(res.headers["Location"], f"/api/jobs/{body['job_id']}")
        self.assertEqual(body["summary"]["total"], 2)
        self.assertEqual(body["event_name"], "Python Bootcamp 2026")

    def test_defaults_applied_for_optional_fields(self):
        res = self.create({"event_name": "Workshop", "recipients": GOOD})
        body = res.get_json()
        self.assertEqual(res.status_code, 202)
        self.assertEqual(body["issuer"], "Example Organization")
        self.assertRegex(body["issue_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_one_request_creates_many_certificates(self):
        many = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(60)]
        body = self.create(payload(many)).get_json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["summary"]["succeeded"], 60)

    def test_unknown_job_is_404(self):
        res = self.client.get("/api/jobs/doesnotexist")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()["error"]["code"], "job_not_found")


class ValidationTests(BaseTest):
    overrides = {"MAX_RECIPIENTS": 3}

    def assert_field_error(self, body, field):
        res = self.create(body)
        self.assertEqual(res.status_code, 422, res.get_json())
        fields = [d["field"] for d in res.get_json()["error"]["details"]]
        self.assertIn(field, fields)

    def test_invalid_json(self):
        res = self.client.post("/api/jobs", data="{not json", content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "invalid_json")

    def test_missing_body(self):
        self.assertEqual(self.client.post("/api/jobs").status_code, 400)

    def test_body_must_be_object(self):
        self.assertEqual(self.create([1, 2]).status_code, 422)

    def test_event_name_required(self):
        self.assert_field_error({"recipients": GOOD}, "event_name")
        self.assert_field_error(payload(event_name="   "), "event_name")

    def test_recipients_required_and_nonempty(self):
        self.assert_field_error({"event_name": "X"}, "recipients")
        self.assert_field_error(payload([]), "recipients")
        self.assert_field_error(payload("alice"), "recipients")

    def test_too_many_recipients(self):
        many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(4)]
        self.assert_field_error(payload(many), "recipients")

    def test_bad_issue_date(self):
        self.assert_field_error(payload(issue_date="01/10/2026"), "issue_date")
        self.assert_field_error(payload(issue_date=20261001), "issue_date")

    def test_recipient_count_at_limit_is_accepted(self):
        three = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(3)]
        self.assertEqual(self.create(payload(three)).status_code, 202)


class RecipientValidationTests(BaseTest):
    def test_bad_recipients_fail_individually_not_the_job(self):
        recipients = [
            {"name": "Valid Person", "email": "valid@example.com"},
            {"name": "", "email": "noname@example.com"},
            {"name": "Bad Email", "email": "not-an-email"},
            {"name": "No Email"},
            "just a string",
            {"name": "Valid Person Again", "email": "VALID@example.com"},
            {"name": "\u4e2d\u6587", "email": "cjk@example.com"},
        ]
        res = self.create(payload(recipients))
        self.assertEqual(res.status_code, 202)
        body = res.get_json()
        self.assertEqual(body["status"], "completed_with_errors")
        self.assertEqual(body["summary"]["succeeded"], 1)
        self.assertEqual(body["summary"]["failed"], 6)
        by_index = {c["index"]: c for c in body["certificates"]}
        self.assertEqual(by_index[0]["status"], "succeeded")
        self.assertIn("name is required", by_index[1]["error"])
        self.assertIn("valid email", by_index[2]["error"])
        self.assertIn("email is required", by_index[3]["error"])
        self.assertIn("JSON object", by_index[4]["error"])
        self.assertIn("Duplicate", by_index[5]["error"])
        self.assertIn("not supported", by_index[6]["error"])

    def test_all_invalid_recipients_marks_job_failed(self):
        body = self.create(payload([{"name": "x"}, {"email": "a@b.co"}])).get_json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["summary"]["failed"], 2)
        self.assertEqual(body["summary"]["progress_percent"], 100.0)

    def test_name_whitespace_is_normalised(self):
        body = self.create(payload([{"name": "  Ada   Lovelace ", "email": " ada@example.com "}])).get_json()
        self.assertEqual(body["certificates"][0]["name"], "Ada Lovelace")
        self.assertEqual(body["certificates"][0]["email"], "ada@example.com")


class CertificateGenerationTests(BaseTest):
    def test_generated_pdf_contains_recipient_information(self):
        body = self.create().get_json()
        cert = body["certificates"][1]
        res = self.client.get(cert["download_url"])
        self.addCleanup(res.close)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))
        text = pdf_text(res.data)
        for expected in ("CERTIFICATE OF COMPLETION", "Python Bootcamp 2026", "Acme Academy", "2026-10-01"):
            self.assertIn(expected, text)
        self.assertIn("Jos", text)
        self.assertIn(cert["certificate_id"], text)

    def test_each_certificate_is_unique_per_recipient(self):
        body = self.create().get_json()
        texts = [pdf_text(self.client.get(c["download_url"]).data) for c in body["certificates"]]
        self.assertIn("Alice Johnson", texts[0])
        self.assertNotIn("Alice Johnson", texts[1])

    def test_pdf_generator_writes_valid_file_without_leftovers(self):
        path = os.path.join(self.tmp.name, "x", "c.pdf")
        long_name = "N" * 100
        certificate.generate_certificate_pdf(path, recipient_name=long_name, event_name="E " * 90,
                                             issuer="I", issue_date="2026-01-01", certificate_id="abc")
        self.assertEqual(len(PdfReader(path).pages), 1)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["c.pdf"])


class StatusAndProgressTests(BaseTest):
    sync = False

    def test_background_job_reports_progress_then_completes(self):
        gate = threading.Event()
        real = certificate.generate_certificate_pdf
        calls = []

        def slow(path, **kw):
            calls.append(kw["recipient_name"])
            if len(calls) == 2:
                gate.wait(10)  # hold the 2nd certificate so we can observe mid-job state
            return real(path, **kw)

        with mock.patch.object(certificate, "generate_certificate_pdf", side_effect=slow):
            job_id = self.create().get_json()["job_id"]
            mid = self.wait_for(job_id, lambda j: j["summary"]["succeeded"] == 1)
            self.assertEqual(mid["status"], "processing")
            self.assertEqual(mid["summary"]["progress_percent"], 50.0)
            self.assertEqual(mid["summary"]["pending"], 1)
            gate.set()
            done = self.wait_for(job_id, lambda j: j["status"] == "completed")
        self.assertEqual(done["summary"]["progress_percent"], 100.0)
        self.assertIsNotNone(done["completed_at"])

    def test_status_filter(self):
        recipients = [{"name": "Good", "email": "g@example.com"}, {"name": "Bad"}]
        job_id = self.create(payload(recipients)).get_json()["job_id"]
        job = self.wait_for(job_id, lambda j: j["status"] != "queued" and j["summary"]["pending"] == 0)
        failed = self.client.get(f"/api/jobs/{job_id}?status=failed").get_json()
        self.assertEqual([c["name"] for c in failed["certificates"]], ["Bad"])
        self.assertEqual(job["summary"]["total"], 2)
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}?status=bogus").status_code, 400)


class IndividualFailureTests(BaseTest):
    def test_generation_error_does_not_stop_other_certificates(self):
        real = certificate.generate_certificate_pdf

        def flaky(path, **kw):
            if kw["recipient_name"] == "Boom Person":
                raise RuntimeError("renderer exploded")
            return real(path, **kw)

        recipients = [{"name": "First", "email": "1@example.com"},
                      {"name": "Boom Person", "email": "2@example.com"},
                      {"name": "Third", "email": "3@example.com"}]
        with mock.patch.object(certificate, "generate_certificate_pdf", side_effect=flaky):
            body = self.create(payload(recipients)).get_json()

        self.assertEqual(body["status"], "completed_with_errors")
        self.assertEqual(body["summary"]["succeeded"], 2)
        self.assertEqual(body["summary"]["failed"], 1)
        statuses = [c["status"] for c in body["certificates"]]
        self.assertEqual(statuses, ["succeeded", "failed", "succeeded"])
        failed = body["certificates"][1]
        self.assertIn("renderer exploded", failed["error"])
        self.assertIsNone(failed["download_url"])

        res = self.client.get(f"/api/jobs/{body['job_id']}/certificates/{failed['certificate_id']}")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"]["code"], "certificate_failed")
        ok = self.client.get(body["certificates"][2]["download_url"])
        self.addCleanup(ok.close)
        self.assertEqual(ok.status_code, 200)


class RetrievalTests(BaseTest):
    def test_download_single_certificate(self):
        body = self.create().get_json()
        res = self.client.get(body["certificates"][0]["download_url"])
        self.addCleanup(res.close)
        self.assertEqual(res.status_code, 200)
        self.assertIn("certificate_", res.headers["Content-Disposition"])
        self.assertEqual(len(PdfReader(io.BytesIO(res.data)).pages), 1)

    def test_unknown_certificate_and_wrong_job_are_404(self):
        a = self.create().get_json()
        b = self.create().get_json()
        self.assertEqual(self.client.get(f"/api/jobs/{a['job_id']}/certificates/nope").status_code, 404)
        foreign = b["certificates"][0]["certificate_id"]
        self.assertEqual(self.client.get(f"/api/jobs/{a['job_id']}/certificates/{foreign}").status_code, 404)

    def test_download_all_as_zip(self):
        recipients = GOOD + [{"name": "Broken"}]
        body = self.create(payload(recipients)).get_json()
        res = self.client.get(body["links"]["download_all"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/zip")
        with zipfile.ZipFile(io.BytesIO(res.data)) as archive:
            names = archive.namelist()
            self.assertEqual(names, ["0001_Alice_Johnson.pdf", "0002_Jos_lvarez.pdf"])
            self.assertTrue(archive.read(names[0]).startswith(b"%PDF"))

    def test_download_all_conflicts(self):
        self.assertEqual(self.client.get("/api/jobs/missing/download").status_code, 404)
        failed_job = self.create(payload([{"name": "x"}])).get_json()
        res = self.client.get(failed_job["links"]["download_all"])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"]["code"], "no_certificates")

    def test_missing_file_on_disk_returns_410(self):
        body = self.create().get_json()
        cert = body["certificates"][0]
        os.remove(os.path.join(self.tmp.name, "certs", body["job_id"], cert["certificate_id"] + ".pdf"))
        self.assertEqual(self.client.get(cert["download_url"]).status_code, 410)


if __name__ == "__main__":
    unittest.main()
