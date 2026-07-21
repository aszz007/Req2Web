from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import req2web_faults  # noqa: E402
from req2web_generation import PageSpecBuilder  # noqa: E402
from req2web_faults.evaluator_gold import (  # noqa: E402
    FaultGoldManifest,
    build_fault_gold_manifest,
    write_fault_gold_manifest,
)
from req2web_faults.injector_audit import build_injector_mutation_audit  # noqa: E402
from req2web_faults.mutation import (  # noqa: E402
    FaultMutationError,
    FaultMutationRequest,
    build_fault_copy,
)
from test_guided_page_spec import build_context  # noqa: E402


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FaultGoldManifestTest(TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_fault_gold_manifest" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.page_spec = PageSpecBuilder().build(build_context())
        self.request = FaultMutationRequest(
            "dev-case-gold", "page_spec_component_removed",
            self.page_spec.components[0].component_id,
        )
        self.build = build_fault_copy(self.request, self.page_spec, self.root / "runtime")
        self.record = self.build.runtime_record
        self.audit = build_injector_mutation_audit(self.build)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def gold(self):
        return build_fault_gold_manifest(
            fault_copy_record=self.record,
            injector_audit=self.audit,
            gold_stage="page_spec",
            gold_error="required_component_removed",
            gold_repairable=True,
            gold_allowed_scope=("components[component_id]",),
            expected_fallback="deterministic_fallback",
        )

    def test_gold_manifest_is_explicit_evaluator_only_and_deterministic(self) -> None:
        first = self.gold()
        second = self.gold()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertTrue(first.evaluator_only)
        self.assertEqual(first.fault_copy_id, self.record.fault_copy_id)
        self.assertEqual(first.fault_copy_sha256, self.record.sha256())
        self.assertEqual(first.injector_audit_id, self.audit.injector_audit_id)
        self.assertEqual(first.injector_audit_sha256, self.audit.sha256())
        write_fault_gold_manifest(first, self.root / "gold-first")
        write_fault_gold_manifest(second, self.root / "gold-second")
        self.assertEqual(tree_bytes(self.root / "gold-first"), tree_bytes(self.root / "gold-second"))
        self.assertEqual(sorted(tree_bytes(self.root / "gold-first")), ["fault_gold_manifest.json"])

    def test_runtime_init_hides_injector_and_evaluator_apis(self) -> None:
        forbidden = (
            "FaultMutationRequest", "build_fault_copy", "write_fault_copy",
            "InjectorMutationAudit", "build_injector_mutation_audit",
            "FaultGoldManifest", "write_fault_gold_manifest",
        )
        for name in forbidden:
            self.assertFalse(hasattr(req2web_faults, name), name)
            self.assertNotIn(name, req2web_faults.__all__)

    def test_gold_manifest_rejects_invalid_stage_and_mismatched_audit(self) -> None:
        with self.assertRaisesRegex(FaultMutationError, "approved D03 stage"):
            build_fault_gold_manifest(
                fault_copy_record=self.record,
                injector_audit=self.audit,
                gold_stage="unapproved_stage",
                gold_error="explicit_error",
                gold_repairable=False,
                gold_allowed_scope=(),
                expected_fallback="deterministic_fallback",
            )
        invalid = replace(self.gold(), evaluator_only=False)
        with self.assertRaisesRegex(FaultMutationError, "evaluator_only"):
            invalid.validate()
        other_request = FaultMutationRequest(
            "dev-case-gold", "page_spec_component_removed",
            self.page_spec.components[-1].component_id,
        )
        other_build = build_fault_copy(other_request, self.page_spec, self.root / "runtime-other")
        other_audit = build_injector_mutation_audit(other_build)
        with self.assertRaisesRegex(FaultMutationError, "does not match runtime record"):
            build_fault_gold_manifest(
                fault_copy_record=self.record,
                injector_audit=other_audit,
                gold_stage="page_spec",
                gold_error="required_component_removed",
                gold_repairable=True,
                gold_allowed_scope=("components[component_id]",),
                expected_fallback="deterministic_fallback",
            )


if __name__ == "__main__":
    import unittest
    unittest.main()
