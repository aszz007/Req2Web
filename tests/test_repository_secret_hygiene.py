from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_repository_secrets import audit_git, main
from repository_secret_rules import find_secret_locations, redact_credential_literals


class CredentialRuleTests(unittest.TestCase):
    def test_smtp_database_and_admin_literals_are_reported_without_values(self):
        value = "synthetic-" + "credential-" + "for-unit-test"
        text = f"email_password = {value}\ndb_password = '{value}'\nharbor_admin_password = {value}\n# database settings\n"
        findings = find_secret_locations(text)
        self.assertEqual([item["line"] for item in findings], [1, 2, 3])
        self.assertNotIn(value, json.dumps(findings))
        self.assertNotIn(value, redact_credential_literals(text))

    def test_empty_assignment_does_not_capture_next_line(self):
        text = "# database\n" + "redis_password" + " =\nredis_host = redis\n"
        self.assertEqual(find_secret_locations(text), [])
        self.assertEqual(redact_credential_literals(text), text)

    def test_environment_references_and_explicit_markers_are_allowed(self):
        text = "\n".join((
            'email_password = "${SMTP_PASSWORD}"',
            'db_password = os.getenv("DB_PASSWORD")',
            'smtp_password = [REDACTED]',
            'db_password = "YOUR_DATABASE_PASSWORD"',
            'db_password = ""',
        ))
        self.assertEqual(find_secret_locations(text), [])

    def test_uri_and_json_encoded_issue_text_are_detected(self):
        value = "unit-" + "test-" + "value"
        uri = "postgresql://reader:" + value + "@invalid.example/db"
        text = json.dumps({"body": "# database\n" + "db_password" + " = " + value}) + "\n" + uri
        self.assertTrue(find_secret_locations(text))
        self.assertNotIn(value, redact_credential_literals(text))

    def test_prefixed_provider_tokens_are_redacted(self):
        fixture = "github_pat_" + "A" * 40
        text = "Credential: " + fixture
        self.assertEqual(find_secret_locations(text)[0]["kind"], "github_token")
        self.assertNotIn(fixture, redact_credential_literals(text))

    def test_pem_redaction_removes_the_payload_not_only_the_header(self):
        marker = "-----" + "BEGIN PRIVATE KEY" + "-----"
        payload = "synthetic" + "PEMpayload"
        text = marker + "\n" + payload + "\n-----END PRIVATE KEY-----\nafter"
        redacted = redact_credential_literals(text)
        self.assertNotIn(payload, redacted)
        self.assertTrue(redacted.endswith("\nafter"))

    def test_redaction_preserves_csv_roundtrip_and_is_idempotent(self):
        value = "fake-" + "password-for-test"
        row = {"id": "reserve", "body": "email_password" + " = " + value + "\nIssue description remains."}
        row["body"] = redact_credential_literals(row["body"])
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        self.assertEqual(next(csv.DictReader(io.StringIO(stream.getvalue()))), row)
        self.assertEqual(redact_credential_literals(row["body"]), row["body"])
        self.assertEqual(find_secret_locations(row["body"]), [])

    @unittest.skipUnless(
        (ROOT / "data/processed/github_issues_prs_review_candidates.csv").is_file(),
        "Private historical dataset is not distributed in the sanitized repository",
    )
    def test_existing_selected_records_do_not_require_redaction(self):
        path = ROOT / "data/processed/github_issues_prs_review_candidates.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        selected = [row for row in rows if row["review_status"] == "auto_selected"]
        self.assertEqual(len(selected), 12)
        for row in selected:
            self.assertEqual(redact_credential_literals(row["issue_body_plain"]), row["issue_body_plain"])


class GitObjectAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="req2web-secret-audit-")
        self.root = Path(self.temporary.name)
        self._git("init", "--quiet")

    def tearDown(self):
        self.temporary.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _commit(self):
        self._git("-c", "user.name=Secret Audit Test", "-c", "user.email=audit@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Synthetic fixture")

    def test_index_checks_staged_blob_not_replaced_worktree(self):
        value = "synthetic-" + "staged-secret"
        path = self.root / "settings.cfg"
        path.write_text("email_password" + " = " + value, encoding="utf-8")
        self._git("add", "settings.cfg")
        path.write_text("email_password = [REDACTED]", encoding="utf-8")
        report = audit_git(self.root)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["findings"][0]["path"], "settings.cfg")
        self.assertNotIn(value, json.dumps(report))

    def test_history_finds_a_value_deleted_from_head(self):
        path = self.root / "settings.cfg"
        path.write_text("db_password" + " = " + "synthetic-" + "history-secret", encoding="utf-8")
        self._git("add", "settings.cfg")
        self._commit()
        path.write_text("db_password = [REDACTED]", encoding="utf-8")
        self._git("add", "settings.cfg")
        self._commit()
        self.assertEqual(audit_git(self.root, "head")["findings"], [])
        self.assertTrue(audit_git(self.root, "history")["findings"])

    def test_non_utf8_text_fails_closed_and_binary_limit_is_explicit(self):
        (self.root / "non_utf8.txt").write_bytes(b"\xff")
        (self.root / "binary.bin").write_bytes(b"\x00\xff")
        self._git("add", ".")
        report = audit_git(self.root)
        self.assertEqual(report["unchecked"][0]["reason"], "non_utf8")
        self.assertEqual(report["binary_blob_count"], 1)
        self.assertEqual(report["status"], "review_required")

    def test_empty_index_is_valid_and_missing_repository_returns_error(self):
        self.assertEqual(audit_git(self.root)["status"], "no_candidates_found")
        self.assertEqual(main(["--root", str(self.root / "missing")]), 2)


if __name__ == "__main__":
    unittest.main()
