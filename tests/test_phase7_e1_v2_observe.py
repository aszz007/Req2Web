from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"


def _load(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


case_module = _load("phase7_e1_v2_cases")
observer = _load("phase7_e1_v2_observe")


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _html(family: str, defect: str | None = None) -> str:
    if family == "ordered_decision":
        reject = "" if defect == "missing_action" else '<button onclick="state=\'Rejected\';render()">Reject</button>'
        return f"""<!doctype html><html><body><main id="app"></main><script>
let state='Pending decision';
function render(){{let controls=state==='Pending decision'?`<button onclick="state='In review';render()">Start review</button><button disabled>Approve</button>`:state==='In review'?`<button onclick="state='Approved';render()">Approve</button>{reject}`:state==='Rejected'?`<button onclick="state='Pending decision';render()">Reset</button>`:'';app.innerHTML=`<h1>${{state}}</h1>${{controls}}`;}}render();
</script></body></html>"""
    if family == "failure_recovery":
        if defect == "nonexistent_failure":
            attempt = "state='Ready';render()"
        else:
            attempt = "state='Attempt failed';render()"
        prose = "<p>Completed</p>" if defect == "inert_success_prose" else ""
        return f"""<!doctype html><html><body>{prose}<main id="app"></main><script>
let state='Ready';function render(){{let controls=state==='Ready'?`<button onclick="{attempt}">Attempt</button><button disabled>Retry</button>`:state==='Attempt failed'?`<button onclick="state='Completed';render()">Retry</button>`:'';app.innerHTML=`<h1>${{state}}</h1>${{controls}}`;}}render();
</script></body></html>"""
    if family == "cancel_restart":
        stale = "<p>Confirmed</p>" if defect == "stale_terminal" else ""
        restart = f"state='Step 1';render();document.body.insertAdjacentHTML('beforeend','{stale}')"
        return f"""<!doctype html><html><body><main id="app"></main><script>
let state='Not started';function render(){{let c=state==='Not started'?`<button onclick="state='Step 1';render()">Start</button><button disabled>Confirm</button>`:state==='Step 1'?`<button onclick="state='Step 2';render()">Next</button><button onclick="state='Not started';render()">Cancel</button>`:state==='Step 2'?`<button onclick="state='Step 1';render()">Back</button><button onclick="state='Step 3';render()">Next</button><button onclick="state='Not started';render()">Cancel</button>`:state==='Step 3'?`<button onclick="state='Confirmed';render()">Confirm</button><button onclick="state='Not started';render()">Cancel</button>`:`<button onclick="{restart}">Restart</button>`;app.innerHTML=`<h1>${{state}}</h1>${{c}}`;}}render();
</script></body></html>"""
    shortcut = '<button onclick="state=\'Completed\';render()">Continue first path</button>' if defect == "illegal_shortcut" else ""
    return f"""<!doctype html><html><body><main id="app"></main><script>
let state='Choose a path';function render(){{let c=state==='Choose a path'?`<button onclick="state='First path active';render()">Choose first path</button><button onclick="state='Second path active';render()">Choose second path</button>{shortcut}`:state==='First path active'?`<button onclick="state='Completed';render()">Continue first path</button><button onclick="state='Choose a path';render()">Reset</button>`:state==='Second path active'?`<button onclick="state='Completed';render()">Continue second path</button><button onclick="state='Choose a path';render()">Reset</button>`:'';app.innerHTML=`<h1>${{state}}</h1>${{c}}`;}}render();
</script></body></html>"""


class Phase7E1V2CaseTests(unittest.TestCase):
    def test_public_packet_is_frozen_complete_and_evaluator_free(self) -> None:
        rows = case_module.cases()
        self.assertEqual(len(rows), 16)
        self.assertEqual(len({row["case_id"] for row in rows}), 16)
        self.assertEqual({row["split"] for row in rows}, {"development", "measured"})
        for family in ("ordered_decision", "failure_recovery", "cancel_restart", "conditional_branch"):
            family_rows = [row for row in rows if row["family"] == family]
            self.assertEqual(sum(row["split"] == "development" for row in family_rows), 1)
            self.assertEqual(sum(row["split"] == "measured" for row in family_rows), 3)
        self.assertEqual(sum(len(observer.obligations_for_case(row["case_id"])) for row in rows), 96)
        self.assertFalse(hasattr(case_module, "obligations_for_case"))
        self.assertFalse(hasattr(case_module, "evaluator_checks"))
        public = json.dumps(rows)
        for forbidden in ("criterion_id", "expected", "selector", "oracle", "gold", "checks"):
            self.assertNotIn(f'"{forbidden}"', public)
        self.assertRegex(case_module.frozen_input_identity(), r"^sha256:[0-9a-f]{64}$")

    def test_each_requirement_contains_its_exact_visible_vocabulary(self) -> None:
        required = {
            "ordered_decision": ("Pending decision", "Start review", "In review", "Approve", "Approved", "Reject", "Rejected", "Reset"),
            "failure_recovery": ("Ready", "Attempt", "Attempt failed", "Retry", "Completed", "Repeat"),
            "cancel_restart": ("Not started", "Start", "Step 1", "Next", "Step 2", "Step 3", "Back", "Cancel", "Confirm", "Confirmed", "Restart"),
            "conditional_branch": ("Choose a path", "Choose first path", "First path active", "Continue first path", "Choose second path", "Second path active", "Continue second path", "Completed", "Reset"),
        }
        for row in case_module.cases():
            for phrase in required[row["family"]]:
                self.assertIn(phrase, row["requirement"], (row["case_id"], phrase))


class Phase7E1V2ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = ROOT / "outputs/phase7_e1_v2_observer"
        self.base.mkdir(parents=True, exist_ok=True)
        self.temp = self.base / f"unit-{uuid.uuid4().hex}"
        self.temp.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_manifest_binds_every_asset_and_hash_drift_is_unavailable(self) -> None:
        page = self.temp / "page.html"
        style = self.temp / "style.css"
        page.write_text("<link rel='stylesheet' href='style.css'>", encoding="utf-8")
        style.write_text("body{}", encoding="utf-8")
        row = {
            "case_id": "e1v2-dev-document-approval", "arm": "A", "entrypoint": "page.html",
            "files": [{"path": "page.html", "sha256": _hash(page)}, {"path": "style.css", "sha256": _hash(style)}],
        }
        manifest = self.temp / "manifest.json"
        manifest.write_text(json.dumps({"rows": [row]}), encoding="utf-8")
        loaded = observer.load_artifact_manifest(self.temp, manifest)
        self.assertTrue(loaded[(row["case_id"], "A")].available)
        style.write_text("body{color:red}", encoding="utf-8")
        loaded = observer.load_artifact_manifest(self.temp, manifest)
        self.assertFalse(loaded[(row["case_id"], "A")].available)
        self.assertIn("hash mismatch", loaded[(row["case_id"], "A")].reason)

    def test_unsafe_path_fails_closed(self) -> None:
        manifest = self.temp / "manifest.json"
        manifest.write_text(json.dumps({"rows": [{
            "case_id": "e1v2-dev-document-approval", "arm": "A", "entrypoint": "../page.html",
            "files": [{"path": "../page.html", "sha256": "sha256:" + "0" * 64}],
        }]}), encoding="utf-8")
        with self.assertRaises(observer.ObservationError):
            observer.load_artifact_manifest(self.temp, manifest)

    def test_development_gate_binds_exact_rows_and_evidence(self) -> None:
        preparation = self.temp / "preparation"
        runtime_root = self.temp / "development/runtime"
        evidence = self.temp / "development/evidence"
        for directory in (preparation, runtime_root, evidence):
            directory.mkdir(parents=True, exist_ok=True)
        freeze = preparation / "freeze.json"
        freeze.write_text("{}", encoding="utf-8")
        runtime_manifest = runtime_root / "return_inventory.json"
        manifest_rows = []
        rows = []
        for case in (row for row in case_module.cases() if row["split"] == "development"):
            for arm in observer.ARMS:
                contract_failure = case["case_id"] == "e1v2-dev-document-approval" and arm == "C"
                consistency_failure = case["case_id"] == "e1v2-dev-sample-download" and arm == "C"
                unavailable = contract_failure or consistency_failure
                manifest_rows.append({
                    "case_id": case["case_id"], "arm": arm,
                    "status": (
                        "failed_or_interrupted" if contract_failure
                        else "failed_closed_consistency" if consistency_failure
                        else "completed_model_delivery" if arm == "A"
                        else "delivered"
                    ),
                    "exit_code": 2 if contract_failure else 0,
                    "started_calls": 1,
                })
                for criterion in observer.obligations_for_case(case["case_id"]):
                    stem = f"{case['case_id']}--{arm}--{criterion}"
                    before = evidence / f"{stem}--before.png"
                    after = evidence / f"{stem}--after.png"
                    if not unavailable:
                        before.write_bytes(b"before")
                        after.write_bytes(b"after")
                    rows.append({
                        "case_id": case["case_id"], "arm": arm,
                        "criterion_id": criterion,
                        "label": "pass" if arm == "A" else "fail",
                        "delivery_available": not unavailable,
                        "delivery_terminal_product_failure": unavailable,
                        "evidence": None if unavailable else {
                            "before": before.relative_to(self.temp / "development").as_posix(),
                            "after": after.relative_to(self.temp / "development").as_posix(),
                        },
                    })
        runtime_manifest.write_text(json.dumps({
            "split": "development", "freeze_sha256": _hash(freeze), "started_calls": 24,
            "stop_reason": None, "rows": manifest_rows,
        }), encoding="utf-8")
        observation_path = self.temp / "development/observation.json"
        observation_path.write_text(json.dumps({
            "status": "observation_complete", "split": "development",
            "input_identity": case_module.frozen_input_identity(),
            "observer_identity": observer.observer_identity(),
            "artifact_manifest_sha256": _hash(runtime_manifest),
            "primary": {"rows": rows},
        }), encoding="utf-8")
        gate = observer.build_development_gate(
            observation=observation_path, artifact_manifest=runtime_manifest,
            freeze=freeze, output=self.temp / "development-gate.json",
        )
        self.assertEqual(gate["development_rows"], 12)
        self.assertEqual(gate["development_available_deliveries"], 10)
        self.assertEqual(gate["development_terminal_product_failures"], 2)
        self.assertEqual(gate["development_started_calls"], 24)
        self.assertTrue(gate["quality_pass_required"])
        self.assertEqual(gate["quality_observed"]["total_passed_obligations"], 24)
        self.assertEqual(len(gate["files"]), 123)


class Phase7E1V2RealBrowserHarnessTests(unittest.TestCase):
    def test_working_and_deliberately_broken_controls(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                probe = playwright.chromium.launch(channel="chrome", headless=True)
                probe.close()
        except Exception as exc:
            self.skipTest(f"local Playwright/Chrome unavailable: {exc}")
        base = ROOT / "outputs/phase7_e1_v2_observer"
        base.mkdir(parents=True, exist_ok=True)
        temp = base / f"browser-{uuid.uuid4().hex}"
        temp.mkdir()
        try:
            dev = [row for row in case_module.cases() if row["split"] == "development"]
            defects = {
                "ordered_decision": "missing_action",
                "failure_recovery": "inert_success_prose",
                "cancel_restart": "stale_terminal",
                "conditional_branch": "illegal_shortcut",
            }
            rows = []
            for case in dev:
                for arm, defect in (("A", None), ("B", defects[case["family"]])):
                    relative = f"{case['case_id']}-{arm}.html"
                    page = temp / relative
                    page.write_text(_html(case["family"], defect), encoding="utf-8")
                    rows.append({"case_id": case["case_id"], "arm": arm, "entrypoint": relative, "files": [{"path": relative, "sha256": _hash(page)}]})
            # A fifth evaluator-negative control: inputs/state are preserved,
            # but the required failure event never occurs.
            recovery = next(row for row in dev if row["family"] == "failure_recovery")
            relative = f"{recovery['case_id']}-C.html"
            page = temp / relative
            page.write_text(_html("failure_recovery", "nonexistent_failure"), encoding="utf-8")
            rows.append({"case_id": recovery["case_id"], "arm": "C", "entrypoint": relative, "files": [{"path": relative, "sha256": _hash(page)}]})
            manifest = temp / "manifest.json"
            manifest.write_text(json.dumps({"rows": rows}), encoding="utf-8")
            output = temp / "observation.json"
            result = observer.observe(
                result_root=temp, manifest_path=manifest, output=output,
                evidence_dir=temp / "evidence", split="development", include_replays=False,
            )
            record = json.loads(output.read_text(encoding="utf-8"))
            primary = record["primary"]["rows"]
            working = [row for row in primary if row["arm"] == "A"]
            self.assertEqual(len(working), 24)
            self.assertTrue(all(row["label"] == "pass" for row in working))
            broken = [row for row in primary if row["arm"] in {"B", "C"} and row["delivery_available"]]
            self.assertGreaterEqual(sum(row["label"] == "fail" for row in broken), 5)
            missing = [row for row in primary if not row["delivery_available"]]
            self.assertTrue(missing)
            self.assertTrue(all(row["label"] == "fail" for row in missing))
            self.assertEqual(result["supplementary_row_count"], 0)
            self.assertLessEqual(max(row["action_count"] for row in primary), 16)
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
