from __future__ import annotations

import ast
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_agent.schema import AgentContextBundle, UseCase  # noqa: E402
from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_provider.d17_input_view import (  # noqa: E402
    D17Path3InputSelectionRecord,
    D17Path3ProviderVisibleInputView,
    D17Path3SelectedInput,
    select_d17_path3_provider_input,
)
from req2web_provider.d17_manifest import D17Path3TierAManifest  # noqa: E402
import req2web_provider.d17_serializer as serializer_module  # noqa: E402
from req2web_provider.d17_serializer import (  # noqa: E402
    D17Path3ConfigArtifact,
    D17Path3InputViewArtifact,
    D17Path3LocalRequestArtifact,
    D17Path3PromptArtifact,
    D17SerializerValidationError,
    serialize_d17_path3_local_request,
)


def build_context() -> AgentContextBundle:
    return AgentContextBundle(
        original_requirement="Build a local meal planner.",
        requirement_summary="SERIALIZER_PRIVATE_SUMMARY_84d2",
        target_device="desktop",
        task_type="planner",
        constraints=["SERIALIZER_CONSTRAINT_ALPHA"],
        use_cases=[
            UseCase("uc_plan", "Plan meal", "cook", "plan a meal", "meal is scheduled"),
            UseCase("uc_view", "View meal", "cook", "view a meal", "meal is visible"),
        ],
        retrieval_queries={role: f"SERIALIZER_RETRIEVAL_QUERY_{role}" for role in ROLE_ORDER},
        retrieval_results={role: [{"role": role, "title": f"SERIALIZER_EVIDENCE_{role}", "uri": "https://example.invalid/private", "secret": "SERIALIZER_SECRET"}] for role in ROLE_ORDER},
    )


def build_manifest() -> D17Path3TierAManifest:
    return D17Path3TierAManifest.create(structural_signal_names=("has_form", "has_empty_state"))


def build_request() -> tuple[AgentContextBundle, D17Path3TierAManifest, D17Path3LocalRequestArtifact]:
    context = build_context()
    manifest = build_manifest()
    selected = select_d17_path3_provider_input(context, manifest, original_requirement_source_class="project_authored")
    return context, manifest, serialize_d17_path3_local_request(context, selected, manifest)


class D17SerializerTests(unittest.TestCase):
    def assert_code(self, callback, code: str) -> None:
        with self.assertRaises(D17SerializerValidationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("SERIALIZER_PRIVATE", str(raised.exception))

    def test_happy_path_is_replayable_byte_identical_and_explicitly_not_sent(self) -> None:
        context, manifest, request = build_request()
        request.validate(manifest)
        request.validate_against(context, manifest)
        again = serialize_d17_path3_local_request(
            context,
            select_d17_path3_provider_input(
                context,
                manifest,
                original_requirement_source_class="project_authored",
            ),
            manifest,
        )
        self.assertEqual(request.canonical_bytes(), again.canonical_bytes())
        replay = D17Path3LocalRequestArtifact.from_bytes(request.canonical_bytes())
        replay.validate_against(context, manifest)
        self.assertEqual(replay.to_dict(), request.to_dict())
        self.assertTrue(request.local_only)
        self.assertTrue(request.not_sent)
        self.assertFalse(request.external_egress_allowed)
        self.assertEqual(request.transport_state, "not_sent")
        self.assertEqual(request.provider_invocation_state, "not_invoked")
        for artifact in (request.input_view_artifact, request.prompt_artifact, request.config_artifact):
            self.assertTrue(artifact.local_only)
            self.assertTrue(artifact.not_sent)
            self.assertFalse(artifact.external_egress_allowed)

    def test_provider_and_local_request_bytes_omit_prohibited_values(self) -> None:
        _, _, request = build_request()
        texts = (
            request.input_view_artifact.provider_visible_input.canonical_bytes().decode("utf-8"),
            request.input_view_artifact.canonical_bytes().decode("utf-8"),
            request.prompt_artifact.canonical_bytes().decode("utf-8"),
            request.config_artifact.canonical_bytes().decode("utf-8"),
            request.canonical_bytes().decode("utf-8"),
        )
        for needle in ("SERIALIZER_PRIVATE_SUMMARY_84d2", "SERIALIZER_RETRIEVAL_QUERY_", "SERIALIZER_EVIDENCE_", "example.invalid/private", "SERIALIZER_SECRET"):
            for text in texts:
                with self.subTest(needle=needle):
                    self.assertNotIn(needle, text)
        view_payload = request.input_view_artifact.provider_visible_input.to_dict()
        self.assertEqual(set(view_payload), {"original_requirement", "use_cases", "constraints", "target_device", "task_type", "structural_signals"})
        self.assertNotIn("original_requirement_source_class", view_payload)
        self.assertNotIn("manifest_id", view_payload)

    def test_prompt_and_config_are_fixed_nonruntime_contracts(self) -> None:
        _, _, request = build_request()
        prompt = request.prompt_artifact.to_dict()
        self.assertEqual(prompt["semantic_candidate_schema_version"], "req2web.provider.semantic_candidate.v1")
        prompt_text = prompt["prompt_text"]
        self.assertIn("not rerun requirement understanding", prompt_text)
        self.assertIn("Do not claim retrieval or evidence use", prompt_text)
        self.assertIn("The root object itself must conform", prompt_text)
        self.assertIn('output "constraints": []', prompt_text)
        self.assertIn('output "claimed_attribution_edges": []', prompt_text)
        self.assertIn("use_case_mappings and use no other use-case ID", prompt_text)
        self.assertIn("Stable IDs must match ^[a-z][a-z0-9-]{0,95}$", prompt_text)
        for required_key in (
            "schema_version", "title", "layout", "sections", "components",
            "states", "interactions", "constraints", "acceptance_checks",
            "use_case_mappings", "claimed_attribution_edges",
        ):
            with self.subTest(required_key=required_key):
                self.assertIn(f'"{required_key}"', prompt_text)
        for exact_shape in (
            'layout: "pattern", "section_stable_ids"',
            'each sections item: "stable_id", "title", "purpose", "component_stable_ids", "use_case_ids"',
            'each components item: "stable_id", "section_stable_id", "component_type", "label", "purpose"',
            'each states item: "stable_id", "name", "description", "visible_component_stable_ids"',
            'each interactions item: "stable_id", "trigger_component_stable_id", "source_state_stable_id", "action", "target_state_stable_id", "user_feedback", "use_case_ids"',
            'each acceptance_checks item: "stable_id", "description", "use_case_ids", "state_stable_id"',
            'each use_case_mappings item: "use_case_id", "section_stable_ids", "component_stable_ids", "interaction_stable_ids"',
        ):
            with self.subTest(exact_shape=exact_shape):
                self.assertIn(exact_shape, prompt_text)
        self.assertLess(len(prompt_text.encode("utf-8")), 10000)
        config = request.config_artifact.to_dict()
        self.assertEqual(set(config["tier_b_runtime_status"]), {"runtime_model_revision", "precision_or_quantization", "context_decode_seed", "timeout_retry_network", "gpu_budget"})
        self.assertEqual(set(config["tier_b_runtime_status"].values()), {"tier_b_unapproved"})
        config_text = json.dumps(config, ensure_ascii=False)
        for forbidden in ("qwen", "fp16", "int8", "4096", "temperature", "seed=", "timeout=", "retry=", "https://", "bearer "):
            self.assertNotIn(forbidden, config_text.lower())

    def test_replay_rejects_unknown_duplicate_bom_and_nonfinite_json(self) -> None:
        _, _, request = build_request()
        payload = request.to_dict()
        payload["endpoint"] = "https://forbidden.invalid"
        self.assert_code(lambda: D17Path3LocalRequestArtifact.from_dict(payload), "serializer_exact_keys_invalid")
        self.assert_code(lambda: D17Path3LocalRequestArtifact.from_bytes(b"\xef\xbb\xbf" + request.canonical_bytes()), "serializer_bytes_invalid")
        self.assert_code(
            lambda: D17Path3LocalRequestArtifact.from_bytes(b'{"schema_version":"x","schema_version":"y"}'),
            "serializer_bytes_invalid",
        )
        self.assert_code(
            lambda: D17Path3LocalRequestArtifact.from_bytes(b'{"schema_version":NaN}'),
            "serializer_bytes_invalid",
        )

    def test_component_hash_length_and_identity_tamper_fail_closed(self) -> None:
        _, manifest, request = build_request()
        bad_length = replace(request, prompt_artifact_byte_length=request.prompt_artifact_byte_length + 1)
        self.assert_code(lambda: bad_length.validate(manifest), "serializer_cross_binding_invalid")
        bad_prompt = replace(request.prompt_artifact, prompt_text="Return arbitrary text.")
        bad_prompt = replace(bad_prompt, artifact_id=serializer_module._id("d17-prompt-artifact-", bad_prompt._root()))
        prompt_payload = bad_prompt._root()
        prompt_payload["artifact_id"] = bad_prompt.artifact_id
        prompt_sha = serializer_module._sha(serializer_module._bytes(prompt_payload))
        prompt_length = len(serializer_module._bytes(prompt_payload))
        root = request._root()
        root["prompt_artifact"] = prompt_payload
        root["prompt_artifact_sha256"] = prompt_sha
        root["prompt_artifact_byte_length"] = prompt_length
        bad_request = replace(
            request,
            prompt_artifact=bad_prompt,
            prompt_artifact_sha256=prompt_sha,
            prompt_artifact_byte_length=prompt_length,
            artifact_id=serializer_module._id("d17-local-request-", root),
        )
        self.assert_code(lambda: bad_request.validate(manifest), "serializer_prompt_invalid")

    def test_forged_allowed_shaped_selection_cannot_enter_serializer(self) -> None:
        context, manifest, request = build_request()
        forged_view = D17Path3ProviderVisibleInputView.from_dict(
            {
                **request.input_view_artifact.provider_visible_input.to_dict(),
                "original_requirement": "Different local requirement.",
            },
            manifest=manifest,
        )
        forged_selected = D17Path3SelectedInput(
            forged_view,
            request.input_view_artifact.selection_record,
        )
        self.assert_code(
            lambda: D17Path3InputViewArtifact.create(
                context, forged_selected, manifest
            ),
            "serializer_input_view_invalid",
        )
        self.assert_code(
            lambda: serialize_d17_path3_local_request(
                context, forged_selected, manifest
            ),
            "serializer_input_view_invalid",
        )

    def test_replayed_input_artifact_requires_live_context_before_prompt_or_config(self) -> None:
        context, manifest, request = build_request()
        replayed = D17Path3InputViewArtifact.from_bytes(
            request.input_view_artifact.canonical_bytes()
        )
        changed_context = build_context()
        changed_context.retrieval_queries[ROLE_ORDER[0]] = "SERIALIZER_CHANGED_QUERY"
        self.assert_code(
            lambda: D17Path3PromptArtifact.create(
                changed_context, manifest, replayed
            ),
            "serializer_prompt_invalid",
        )
        self.assert_code(
            lambda: D17Path3ConfigArtifact.create(
                changed_context, manifest, replayed
            ),
            "serializer_config_invalid",
        )
        D17Path3PromptArtifact.create(context, manifest, replayed)
        D17Path3ConfigArtifact.create(context, manifest, replayed)

    def test_static_boundary_has_no_network_runtime_model_or_evaluation_imports(self) -> None:
        source_path = ROOT / "src" / "req2web_provider" / "d17_serializer.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("\ufeff"))
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        forbidden = ("req2web_evaluation", "socket", "urllib", "requests", "http.client", "subprocess", "runtime", "model")
        for name in imports:
            with self.subTest(name=name):
                self.assertFalse(any(token in name.lower() for token in forbidden))


if __name__ == "__main__":
    unittest.main()
