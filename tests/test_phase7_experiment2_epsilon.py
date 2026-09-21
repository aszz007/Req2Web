"""Focused projection/protocol tests; no measured external runs."""
import ast
import base64
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('epsilon_comparison', ROOT / 'scripts/phase7_experiment2_epsilon.py')
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


class EpsilonComparisonTests(unittest.TestCase):
    def test_projection_preserves_all_input_bytes_and_adds_no_verdicts(self):
        bundle = experiment.HISTORY / 'detector_input/slot-001'
        root = ET.fromstring(experiment.project(bundle))
        raw = {node.attrib['path']: base64.b64decode(node.text) for node in root.findall('artifact')}
        manifest = json.loads(raw['fault_case_bundle_manifest.json'])
        self.assertEqual(set(raw), {r['path'] for r in manifest['files']} | {'fault_case_bundle_manifest.json'})
        for row in manifest['files']:
            self.assertEqual(raw[row['path']], (bundle / row['path']).read_bytes())
        prohibited = {'detected', 'failed', 'verdict', 'mutation_kind', 'gold_target', 'expected_error_code'}
        for node in root:
            self.assertFalse(prohibited.intersection(node.attrib))
        build = json.loads(raw['artifact/result_package/internal/guided_page_spec_build_result.json'])
        count = sum(len(r['affected_fields']) for group in ('adopted', 'ignored', 'fallback') for r in build[group])
        self.assertEqual(len(root.findall('affected')), count)

    def test_projection_does_not_import_req2web_diagnostics(self):
        tree = ast.parse(Path(experiment.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or '').startswith('req2web'))
            if isinstance(node, ast.Import):
                self.assertTrue(all(not name.name.startswith('req2web') for name in node.names))

    def test_native_report_decodes_rule_and_target(self):
        line = base64.b64encode(b'DeclaredDigestMatches\tfile|internal/file.json')
        raw = b'EPSILON_RESULT_V1\n' + line + b'\nEPSILON_END 1\n'
        with patch.object(experiment, 'call', return_value=raw):
            result = experiment.execute(Path('toy.xml'), False, Path('not-written'))
        self.assertEqual(result['target_candidates'], ['file|internal/file.json'])
        self.assertTrue(result['detected'])

    def test_partial_native_report_is_not_success(self):
        with patch.object(experiment, 'call', return_value=b'EPSILON_RESULT_V1\n'):
            with self.assertRaisesRegex(ValueError, 'framing'):
                experiment.execute(Path('toy.xml'), False, Path('not-written'))

    def test_existing_outputs_cannot_be_overwritten(self):
        with self.assertRaises(FileExistsError):
            experiment.write(Path(__file__), b'forbidden')


if __name__ == '__main__':
    unittest.main()
