"""Small adapter checks; do not repeat the external run or invoke a model."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('external_probe', ROOT/'scripts/phase7_experiment2_external_probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ExternalProbeTests(unittest.TestCase):
    def test_empty_manifest_export_is_not_fabricated_trajectory(self):
        bundle = Mock()
        bundle.__truediv__ = Mock(return_value=Mock(read_bytes=Mock(return_value=b'{"files":[]}')))
        payload = probe.artifact_payload(bundle)
        self.assertEqual(set(payload), {'manifest', 'artifacts'})
        self.assertNotIn('events', payload)

    def test_unsafe_manifest_path_rejected(self):
        bundle = Mock()
        raw = json.dumps({'files': [{'path': '../labels.json'}]}).encode()
        bundle.__truediv__ = Mock(return_value=Mock(read_bytes=Mock(return_value=raw)))
        with self.assertRaisesRegex(ValueError, 'unsafe artifact path'):
            probe.artifact_payload(bundle)

    def test_existing_probe_output_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, 'never overwrite'):
            probe.probe(Mock(), Mock(exists=Mock(return_value=True)), Mock())


if __name__ == '__main__':
    unittest.main()
