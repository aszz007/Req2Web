from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/phase7_e1_browser_observation.py"
    spec = importlib.util.spec_from_file_location("phase7_e1_browser_observation", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observation = load_module()


class Phase7E1BrowserObservationTests(unittest.TestCase):
    def test_binding_inventory_is_complete_and_arm_blind(self) -> None:
        self.assertEqual(set(observation.BINDING_SPECS), set(observation.EXPECTED_ALIASES))
        self.assertEqual(sum(len(value) for value in observation.CASE_OBLIGATIONS.values()), 48)
        self.assertEqual(
            sum(len(observation.CASE_OBLIGATIONS[row["case_id"]]) for row in observation.BINDING_SPECS.values()),
            96,
        )
        self.assertNotIn("arm", json.dumps(observation.BINDING_SPECS))

    def test_generic_renderer_bindings_do_not_treat_requirement_prose_as_behavior(self) -> None:
        model_aliases = (
            "artifact-01", "artifact-04", "artifact-05", "artifact-08",
            "artifact-09", "artifact-12", "artifact-13", "artifact-16",
            "artifact-17", "artifact-20", "artifact-21", "artifact-24",
        )
        for alias in model_aliases:
            controls = observation.BINDING_SPECS[alias]["controls"]
            self.assertTrue(all(value is None for value in controls.values()), alias)

    def test_adjudications_are_narrow_and_remove_only_explained_unknowns(self) -> None:
        self.assertEqual(len(observation.ADJUDICATIONS), 7)
        unknown_changes = [value for value in observation.ADJUDICATIONS.values() if value[:2] == ("unknown", "fail")]
        self.assertEqual(len(unknown_changes), 3)
        self.assertTrue(all("disabled" in value[2] for value in unknown_changes))

    def test_freeze_writes_exact_alias_only_packet(self) -> None:
        discovery = {
            "schema_version": f"{observation.SCHEMA}.discovery",
            "artifact_inventory_sha256": "sha256:" + "a" * 64,
            "artifacts": [
                {"alias": alias, "artifact_sha256": "sha256:" + f"{index:064x}"}
                for index, alias in enumerate(observation.EXPECTED_ALIASES, 1)
            ],
        }
        discovery_path = MagicMock()
        discovery_path.read_bytes.return_value = b"frozen-discovery"
        captured = {}
        with patch.object(observation, "_read", return_value=discovery), patch.object(
            observation, "_write_new", side_effect=lambda _path, value: captured.update(packet=value)
        ):
            result = observation.freeze_bindings(discovery_path=discovery_path, output=Path("bindings.json"))
        packet = captured["packet"]
        self.assertEqual(result["binding_count"], 24)
        self.assertFalse(packet["arm_assignment_visible_to_observer"])
        self.assertEqual(len(packet["bindings"]), 24)
        self.assertNotIn('"arm"', json.dumps(packet))


if __name__ == "__main__":
    unittest.main()
