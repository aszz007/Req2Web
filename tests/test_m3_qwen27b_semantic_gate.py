from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from req2web_provider.semantic_candidate import ModelSemanticCandidate
from req2web_runtime.qwen27b_semantic_gate import (  # noqa: E402
    Qwen27bSemanticCoverageReport,
    Qwen27bSemanticGateError,
    evaluate_qwen27b_semantic_coverage,
    evaluate_qwen27b_semantic_coverage_after_route,
)


def payload(case_id: str) -> dict:
    prefix = "commerce" if case_id == "path3-commerce-checkout" else "media"
    terms = {
        "commerce": [
            ("Search catalog", "search query filter refine product results", "Search product", "Search results updated"),
            ("Add to cart", "add item cart basket checkout order", "Add item", "Cart item added"),
            ("Submit order", "checkout order submit delivery address form invalid error validation retry correct preserve", "Submit order", "Order validation shown"),
        ],
        "media": [
            ("Upload media", "upload file photo image media select input browse permission denied error retry fallback recover", "Select file", "Upload selected or recovery shown"),
            ("Analyze media", "analyze analysis result output insight", "Analyze", "Analysis result shown"),
        ],
    }[prefix]
    ucs = ["UC-01", "UC-02", "UC-03"] if prefix == "commerce" else ["UC-01", "UC-02"]
    components = []
    states = []
    interactions = []
    sections = []
    checks = []
    mappings = []
    for index, (title, purpose, label, feedback) in enumerate(terms, 1):
        section = f"sec-{prefix}-{index}"
        component = f"cmp-{prefix}-{index}"
        source, target = f"st-{prefix}-{index}-source", f"st-{prefix}-{index}-target"
        interaction = f"int-{prefix}-{index}"
        sections.append({"stable_id": section, "title": title, "purpose": purpose, "component_stable_ids": [component], "use_case_ids": [ucs[index - 1]]})
        components.append({"stable_id": component, "section_stable_id": section, "component_type": "button", "label": label, "purpose": purpose})
        states += [
            {"stable_id": source, "name": title + " input", "description": purpose, "visible_component_stable_ids": [component]},
            {"stable_id": target, "name": title + " result", "description": feedback, "visible_component_stable_ids": [component]},
        ]
        interactions.append({"stable_id": interaction, "trigger_component_stable_id": component, "source_state_stable_id": source, "action": purpose, "target_state_stable_id": target, "user_feedback": feedback, "use_case_ids": [ucs[index - 1]]})
        checks.append({"stable_id": f"acc-{prefix}-{index}", "description": f"The user sees {feedback.lower()}.", "use_case_ids": [ucs[index - 1]], "state_stable_id": target})
        mappings.append({"use_case_id": ucs[index - 1], "section_stable_ids": [section], "component_stable_ids": [component], "interaction_stable_ids": [interaction]})
    # Add enough per-UC components/interactions to meet the frozen cardinalities.
    for index, uc in enumerate(ucs, 1):
        need_components = (
            3
            if prefix == "commerce" and index == 3
            else 2
            if prefix == "commerce" or (prefix == "media" and index == 1)
            else 1
        )
        need_interactions = (
            2
            if prefix == "commerce" or (prefix == "media" and index == 1)
            else 1
        )
        base = mappings[index - 1]
        section = base["section_stable_ids"][0]
        source = f"st-{prefix}-{index}-source"
        target = f"st-{prefix}-{index}-target"
        for extra in range(2, need_components + 1):
            component = f"cmp-{prefix}-{index}-{extra}"
            components.append({"stable_id": component, "section_stable_id": section, "component_type": "input", "label": terms[index - 1][0], "purpose": terms[index - 1][1]})
            sections[index - 1]["component_stable_ids"].append(component)
            base["component_stable_ids"].append(component)
            states[[item["stable_id"] for item in states].index(source)]["visible_component_stable_ids"].append(component)
            states[[item["stable_id"] for item in states].index(target)]["visible_component_stable_ids"].append(component)
        for extra in range(2, need_interactions + 1):
            component = base["component_stable_ids"][extra - 1]
            interaction = f"int-{prefix}-{index}-{extra}"
            interactions.append({"stable_id": interaction, "trigger_component_stable_id": component, "source_state_stable_id": source, "action": terms[index - 1][1], "target_state_stable_id": target, "user_feedback": terms[index - 1][3], "use_case_ids": [uc]})
            base["interaction_stable_ids"].append(interaction)
    return {
        "schema_version": "req2web.provider.semantic_candidate.v1", "title": prefix + " workflow", "layout": {"pattern": "single column", "section_stable_ids": [item["stable_id"] for item in sections]},
        "sections": sections, "components": components, "states": states, "interactions": interactions, "constraints": [], "acceptance_checks": checks, "use_case_mappings": mappings, "claimed_attribution_edges": [],
    }


class Qwen27bSemanticGateTests(unittest.TestCase):
    def gate(self, case_id: str, data: dict | None = None, **statuses):
        candidate = ModelSemanticCandidate.from_dict(data or payload(case_id))
        return evaluate_qwen27b_semantic_coverage(case_id=case_id, candidate=candidate, parser_status=statuses.get("parser", "passed"), assembler_status=statuses.get("assembler", "passed"))

    def test_semantically_sufficient_pre_route_coverage_remains_pending(self):
        for case_id in ("path3-commerce-checkout", "path3-media-analysis"):
            report = self.gate(case_id)
            self.assertEqual(report.to_dict()["decision"], "pending")
            self.assertEqual(Qwen27bSemanticCoverageReport.from_bytes(report.canonical_bytes()).sha256(), report.sha256())

    def test_shared_terminal_bridge_uses_other_independent_semantics(self):
        data = payload("path3-media-analysis")
        terminal = next(
            item for item in data["interactions"] if item["stable_id"] == "int-media-2"
        )
        terminal["use_case_ids"] = ["UC-01", "UC-02"]
        uc01_mapping = next(
            item for item in data["use_case_mappings"] if item["use_case_id"] == "UC-01"
        )
        uc01_mapping["interaction_stable_ids"].append("int-media-2")

        report = self.gate("path3-media-analysis", data)
        self.assertEqual(report.to_dict()["decision"], "pending")
        uc02 = report.to_dict()["use_case_coverage"][1]
        self.assertEqual(uc02["exclusive_interaction_ids"], [])
        self.assertIn("component", uc02["independent_contribution_kinds"])
        self.assertIn("acceptance_check", uc02["independent_contribution_kinds"])
        self.assertNotIn(
            "independent_semantic_contribution_missing",
            uc02["failure_codes"],
        )

    def test_identical_interaction_sets_are_allowed_with_other_independent_semantics(self):
        data = payload("path3-media-analysis")
        shared_ids = [item["stable_id"] for item in data["interactions"]]
        for interaction in data["interactions"]:
            interaction["use_case_ids"] = ["UC-01", "UC-02"]
        for mapping in data["use_case_mappings"]:
            mapping["interaction_stable_ids"] = list(shared_ids)

        report = self.gate("path3-media-analysis", data)
        self.assertEqual(report.to_dict()["decision"], "pending")
        for row in report.to_dict()["use_case_coverage"]:
            self.assertEqual(row["exclusive_interaction_ids"], [])
            self.assertTrue(row["independent_contribution_kinds"])
            self.assertNotIn(
                "independent_semantic_contribution_missing",
                row["failure_codes"],
            )

    def test_collapsed_visibility_free_commerce_is_rejected(self):
        # This remains a strict candidate but reproduces the failed local-spike
        # shape: every commerce UC is mapped to one search interaction and no
        # state exposes its trigger/acceptance component.
        data = {
            "schema_version": "req2web.provider.semantic_candidate.v1", "title": "Collapsed commerce", "layout": {"pattern": "single", "section_stable_ids": ["sec-search"]},
            "sections": [{"stable_id": "sec-search", "title": "Search", "purpose": "search query product result", "component_stable_ids": ["cmp-search"], "use_case_ids": ["UC-01", "UC-02", "UC-03"]}],
            "components": [{"stable_id": "cmp-search", "section_stable_id": "sec-search", "component_type": "input", "label": "Search", "purpose": "search query"}],
            "states": [{"stable_id": "st-search", "name": "Search", "description": "Search results", "visible_component_stable_ids": []}, {"stable_id": "st-results", "name": "Results", "description": "Search results", "visible_component_stable_ids": []}],
            "interactions": [{"stable_id": "int-search", "trigger_component_stable_id": "cmp-search", "source_state_stable_id": "st-search", "action": "search query", "target_state_stable_id": "st-results", "user_feedback": "search results", "use_case_ids": ["UC-01", "UC-02", "UC-03"]}],
            "constraints": [],
            "acceptance_checks": [
                {"stable_id": "acc-search-shared", "description": "The shared search results are visible.", "use_case_ids": ["UC-01", "UC-02", "UC-03"], "state_stable_id": "st-results"},
            ],
            "use_case_mappings": [{"use_case_id": uc, "section_stable_ids": ["sec-search"], "component_stable_ids": ["cmp-search"], "interaction_stable_ids": ["int-search"]} for uc in ("UC-01", "UC-02", "UC-03")],
            "claimed_attribution_edges": [],
        }
        report = self.gate("path3-commerce-checkout", data)
        failures = report.to_dict()["failure_codes"]
        self.assertEqual(report.to_dict()["decision"], "fail_closed")
        self.assertTrue(
            all(
                f"{use_case_id.lower().replace('-', '_')}_independent_semantic_contribution_missing"
                in failures
                for use_case_id in ("UC-01", "UC-02", "UC-03")
            )
        )
        self.assertTrue(any("trigger_not_visible" in code or "acceptance_state_visibility" in code for code in failures))

    def test_pending_generic_gate_never_claims_pass(self):
        report = self.gate("path3-media-analysis")
        self.assertEqual(report.to_dict()["decision"], "pending")
        self.assertEqual(report.to_dict()["failure_codes"], [])
        candidate = ModelSemanticCandidate.from_dict(
            payload("path3-media-analysis")
        )
        with self.assertRaisesRegex(
            Qwen27bSemanticGateError,
            "generic_route_outcome_type_invalid",
        ):
            evaluate_qwen27b_semantic_coverage_after_route(
                case_id="path3-media-analysis",
                candidate=candidate,
                route_outcome=object(),
            )
        with self.assertRaises(TypeError):
            evaluate_qwen27b_semantic_coverage(
                case_id="path3-media-analysis",
                candidate=candidate,
                parser_status="passed",
                assembler_status="passed",
                generic_gate_status="passed",
            )

    def test_cross_case_and_tamper_are_rejected(self):
        with self.assertRaisesRegex(Qwen27bSemanticGateError, "case_id_not_registered"):
            self.gate("unregistered-case")
        candidate = ModelSemanticCandidate.from_dict(payload("path3-commerce-checkout"))
        with self.assertRaisesRegex(Qwen27bSemanticGateError, "model_semantic_candidate_type_invalid"):
            evaluate_qwen27b_semantic_coverage(case_id="path3-commerce-checkout", candidate=object(), parser_status="passed", assembler_status="passed")
        report = self.gate("path3-commerce-checkout")
        raw = bytearray(report.canonical_bytes()); raw[-2] = ord("x")
        with self.assertRaises(Qwen27bSemanticGateError):
            Qwen27bSemanticCoverageReport.from_bytes(bytes(raw))
        malformed_hash = copy.deepcopy(report.to_dict())
        malformed_hash["candidate_sha256"] = "g" * 64
        with self.assertRaisesRegex(
            Qwen27bSemanticGateError, "candidate_hash_invalid"
        ):
            Qwen27bSemanticCoverageReport(malformed_hash).canonical_bytes()
        forged_pending = self.gate("path3-commerce-checkout").to_dict()
        forged_pending["failure_codes"] = ["forged_failure"]
        with self.assertRaisesRegex(
            Qwen27bSemanticGateError, "decision_invalid"
        ):
            Qwen27bSemanticCoverageReport(forged_pending).canonical_bytes()
        malformed_coverage = copy.deepcopy(report.to_dict())
        malformed_coverage["use_case_coverage"][0]["extra"] = True
        with self.assertRaisesRegex(
            Qwen27bSemanticGateError, "coverage_invalid"
        ):
            Qwen27bSemanticCoverageReport(malformed_coverage).canonical_bytes()
        with self.assertRaisesRegex(Qwen27bSemanticGateError, "input_status_invalid"):
            self.gate("path3-commerce-checkout", parser="pending_existing_route_replay")


if __name__ == "__main__":
    unittest.main()
