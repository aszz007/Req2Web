from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import shutil
import sys
from unittest import TestCase


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_generation import (  # noqa: E402
    DeterministicPageRenderer,
    DeterministicRetrievalEnhancedResultPackager,
    MinimalConsistencyChecker,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
import req2web_evaluation.frozen_g0_reference as frozen_reference  # noqa: E402
from req2web_evaluation.frozen_g0_reference import (  # noqa: E402
    FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION,
    FrozenG0PackageReference,
    FrozenG0PackageReferenceError,
    freeze_verified_g0_package_reference,
)
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from test_guided_page_spec import build_context  # noqa: E402


class FrozenG0PackageReferenceTest(TestCase):
    def setUp(self) -> None:
        self.work = ROOT / "outputs" / "_m3_frozen_g0_reference_tests" / self._testMethodName
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.work, ignore_errors=True)

    def _package(self, name: str = "fixture"):
        context = build_context()
        guidance = RetrievalGuidanceBuilder().build(context)
        baseline = PageSpecBuilder().build(context)
        guided = RetrievalGuidedPageSpecBuilder().build(context, guidance)
        ablations = {
            role: RetrievalGuidedPageSpecBuilder().build(context, guidance, disabled_roles=(role,))
            for role in ROLE_ORDER
        }
        render = DeterministicPageRenderer().render(guided.page_spec, self.work / name / "render")
        consistency = MinimalConsistencyChecker().check(guided.page_spec, render)
        influence = RetrievalInfluenceChecker().check(context, guidance, baseline, guided, ablations, render)
        package = DeterministicRetrievalEnhancedResultPackager().package(
            context,
            guidance,
            guided,
            guided.page_spec,
            render,
            consistency,
            influence,
            self.work / name / "package",
        )
        package.validate()
        return package, context, guidance

    def _assert_error(self, callback, code: str) -> None:
        with self.assertRaises(FrozenG0PackageReferenceError) as raised:
            callback()
        self.assertEqual(
            (raised.exception.code, raised.exception.stage, raised.exception.phase),
            (code, "package", "g0_freeze_reference"),
        )

    def _with_recomputed_reference_id(self, reference, **changes):
        candidate = replace(reference, **changes, reference_id="")
        return replace(
            candidate,
            reference_id=frozen_reference._reference_id(
                package_id=candidate.package_id,
                page_id=candidate.page_id,
                context_id=candidate.context_id,
                context_sha256=candidate.context_sha256,
                guidance_bundle_id=candidate.guidance_bundle_id,
                guidance_sha256=candidate.guidance_sha256,
                package_manifest_sha256=candidate.package_manifest_sha256,
                inventory_tree_sha256=candidate.inventory_tree_sha256,
                inventory=candidate.inventory,
            ),
        )

    def test_happy_path_is_path_free_and_declares_only_non_executed_downstream_status(self) -> None:
        package, context, guidance = self._package()
        reference = freeze_verified_g0_package_reference(package, context, guidance)
        reference.validate_against(package, context, guidance)
        payload = reference.to_dict()
        self.assertEqual(payload["schema_version"], FROZEN_G0_PACKAGE_REFERENCE_SCHEMA_VERSION)
        self.assertEqual(tuple(item["relative_path"] for item in payload["inventory"]), (
            "internal/agent_context.json", "internal/consistency_report.json",
            "internal/guided_page_spec_build_result.json", "internal/page_spec.json",
            "internal/retrieval_guidance.json", "internal/retrieval_influence_report.json",
            "package_manifest.json", "page/app.js", "page/index.html", "page/render_manifest.json",
            "page/styles.css", "result_summary.json",
        ))
        self.assertEqual(payload["declarations"], {
            "baseline_kind": "deterministic_guided_g0", "retrieval_influence": "v1_only",
            "result_package": "v2_only", "package_copied": False, "fallback_materialized": False,
            "delivery_route": "not_executed", "provider_invocation_status": "not_executed",
            "unguided_builder_used": False,
        })
        encoded = repr(payload)
        self.assertNotIn(str(package.package_dir), encoded)
        self.assertNotIn(context.original_requirement, encoded)

    def test_same_package_is_deterministic_and_reference_creation_does_not_modify_files(self) -> None:
        package, context, guidance = self._package()
        before = {
            path.relative_to(package.package_dir).as_posix(): path.read_bytes()
            for path in package.package_dir.rglob("*") if path.is_file()
        }
        first = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        second = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        after = {
            path.relative_to(package.package_dir).as_posix(): path.read_bytes()
            for path in package.package_dir.rglob("*") if path.is_file()
        }
        self.assertEqual(first, second)
        self.assertEqual(before, after)

    def test_wrong_context_or_guidance_fails_closed(self) -> None:
        package, context, guidance = self._package()
        self._assert_error(
            lambda: FrozenG0PackageReference.from_verified_package(
                package, replace(context, task_type="other"), guidance
            ),
            "context_package_binding_mismatch",
        )
        self._assert_error(
            lambda: FrozenG0PackageReference.from_verified_package(
                package, context, replace(guidance, target_device="desktop")
            ),
            "guidance_package_binding_mismatch",
        )

    def test_post_freeze_tamper_missing_extra_and_wrong_package_schema_fail_closed(self) -> None:
        package, context, guidance = self._package()
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        (package.package_dir / "page" / "app.js").write_text("tampered\n", encoding="utf-8")
        self._assert_error(lambda: reference.validate_against(package, context, guidance), "package_or_live_artifact_validation_failed")

        package, context, guidance = self._package("missing")
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        (package.package_dir / "page" / "app.js").unlink()
        self._assert_error(lambda: reference.validate_against(package, context, guidance), "package_or_live_artifact_validation_failed")

        package, context, guidance = self._package("extra")
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        (package.package_dir / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        self._assert_error(lambda: reference.validate_against(package, context, guidance), "package_or_live_artifact_validation_failed")

        package, context, guidance = self._package("schema")
        self._assert_error(
            lambda: FrozenG0PackageReference.from_verified_package(
                replace(package, schema_version="wrong"), context, guidance
            ),
            "package_or_live_artifact_validation_failed",
        )

    def test_standalone_identity_and_inventory_tamper_fail_closed(self) -> None:
        package, context, guidance = self._package()
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        self._assert_error(lambda: replace(reference, reference_id="wrong").validate(), "reference_identity_invalid")
        self._assert_error(
            lambda: replace(reference, inventory=tuple(reversed(reference.inventory))).validate(),
            "inventory_order_invalid",
        )
        self._assert_error(
            lambda: replace(reference, inventory_tree_sha256="0" * 64).validate(),
            "tree_sha256_invalid",
        )

    def test_basic_identity_and_hash_errors_use_supported_fixed_envelopes(self) -> None:
        package, context, guidance = self._package()
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        cases = (
            ("package_id", "", "package_identity_invalid", "frozen G0 reference package identity is invalid"),
            ("page_id", "", "page_identity_invalid", "frozen G0 reference page identity is invalid"),
            ("guidance_bundle_id", "", "guidance_identity_invalid", "frozen G0 reference guidance identity is invalid"),
            ("context_sha256", "not-a-sha256", "context_sha256_invalid", "frozen G0 reference context hash is invalid"),
            ("guidance_sha256", "not-a-sha256", "guidance_sha256_invalid", "frozen G0 reference guidance hash is invalid"),
            ("package_manifest_sha256", "not-a-sha256", "manifest_sha256_invalid", "frozen G0 reference manifest hash is invalid"),
        )
        for field, value, code, message in cases:
            with self.subTest(field=field):
                invalid = replace(reference, **{field: value})
                with self.assertRaises(FrozenG0PackageReferenceError) as raised:
                    invalid.validate()
                self.assertEqual(
                    (raised.exception.code, raised.exception.stage, raised.exception.phase, str(raised.exception)),
                    (code, "package", "g0_freeze_reference", message),
                )

    def test_safe_error_envelope_rejects_unknown_codes(self) -> None:
        error = FrozenG0PackageReferenceError("context_schema_invalid")
        self.assertEqual(str(error), "frozen G0 reference schema is invalid")
        self.assertNotIn(error.code, str(error))
        with self.assertRaisesRegex(ValueError, "unsupported frozen G0 package reference error code"):
            FrozenG0PackageReferenceError("untrusted-content-must-not-appear")

    def test_declarations_are_not_mutable_and_root_inventory_bindings_fail_closed_after_recomputation(self) -> None:
        package, context, guidance = self._package()
        reference = FrozenG0PackageReference.from_verified_package(package, context, guidance)
        self.assertFalse(hasattr(reference, "declarations"))
        with self.assertRaises(FrozenInstanceError):
            reference.declarations = {"baseline_kind": "tampered"}
        projected = reference.to_dict()
        projected["declarations"]["baseline_kind"] = "tampered"
        self.assertEqual(reference.to_dict()["declarations"]["baseline_kind"], "deterministic_guided_g0")

        root_cases = (
            ("context_sha256", "context_inventory_binding_invalid"),
            ("guidance_sha256", "guidance_inventory_binding_invalid"),
            ("package_manifest_sha256", "package_manifest_inventory_binding_invalid"),
        )
        for field, code in root_cases:
            with self.subTest(field=field):
                changed = {field: "0" * 64}
                if field == "context_sha256":
                    changed["context_id"] = "agent-context-" + ("0" * 20)
                tampered = self._with_recomputed_reference_id(reference, **changed)
                self.assertNotEqual(tampered.reference_id, reference.reference_id)
                self._assert_error(tampered.validate, code)

    def test_controlled_symlink_is_rejected(self) -> None:
        package, context, guidance = self._package()
        target = package.package_dir / "page" / "app.js"
        link = package.package_dir / "page" / "linked.js"
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {type(exc).__name__}")
        self._assert_error(
            lambda: FrozenG0PackageReference.from_verified_package(package, context, guidance),
            "package_or_live_artifact_validation_failed",
        )

    def test_source_and_contract_are_utf8_without_bom_and_have_no_provider_or_gold_reverse_import(self) -> None:
        for path in (
            ROOT / "src" / "req2web_evaluation" / "frozen_g0_reference.py",
            ROOT / "tests" / "test_m3_frozen_g0_reference.py",
            ROOT / "docs" / "stage3_m3_frozen_g0_reference_contract.md",
        ):
            content = path.read_bytes()
            self.assertFalse(content.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertNotIn(b"\r\n", content, path.name)
        source = (ROOT / "src" / "req2web_evaluation" / "frozen_g0_reference.py").read_text(encoding="utf-8")
        for forbidden in ("req2web_provider", "qwen", "gold", "h1", "subprocess", "requests", "urllib"):
            self.assertNotIn(forbidden, source.lower())
