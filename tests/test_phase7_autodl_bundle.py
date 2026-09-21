"""Focused local-only archive checks; not a remote or model experiment."""
import importlib.util
import json
from pathlib import Path
import unittest
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('p7bundle',ROOT/'scripts/prepare_phase7_autodl_bundle.py')
bundle=importlib.util.module_from_spec(spec); spec.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=ROOT/'outputs/phase7_autodl_prep_v1/bundle-checks'/uuid.uuid4().hex
        cls.root.mkdir(parents=True)

    def sample(self,name):
        root=self.root/name
        result=bundle.write_archive({'scripts/example.py':b'print(1)\n'},root,{'source_commit':'a'*40})
        return root,result

    def test_roundtrip_manifest_and_hash(self):
        root, result=self.sample('roundtrip')
        self.assertEqual(bundle.validate_archive(root/result['archive_filename'],result['archive_sha256'])['files'],1)

    def test_runtime_dependency_authority_files_are_explicitly_included(self):
        self.assertIn(
            'docs/phase4_langgraph_dependency_acquisition_receipt.json',
            bundle.EXPLICIT_FILES,
        )
        self.assertIn('requirements-phase4-agent.txt', bundle.EXPLICIT_FILES)

    def test_refuses_overwrite(self):
        root,_=self.sample('existing')
        with self.assertRaisesRegex(ValueError,'already exists'):
            bundle.write_archive({},root,{'source_commit':'b'*40})

    def test_rejects_unsafe_paths(self):
        for name in ('../outside','/root/private','C:/private','a\\b','a/../b','a//b'):
            with self.subTest(name=name),self.assertRaises(ValueError): bundle.safe_path(name)

    def test_rejects_member_tampering(self):
        root,_=self.sample('tamper')
        original=root/'phase7-autodl-payload.zip'; modified=root/'modified.zip'
        with zipfile.ZipFile(original) as src,zipfile.ZipFile(modified,'x') as dst:
            for name in src.namelist(): dst.writestr(name,b'print(2)\n' if name.endswith('.py') else src.read(name))
        with self.assertRaisesRegex(ValueError,'byte drift'): bundle.validate_archive(modified)

    def test_rejects_added_member(self):
        root,_=self.sample('extra'); path=root/'phase7-autodl-payload.zip'
        with zipfile.ZipFile(path,'a') as z: z.writestr('unlisted.txt','extra')
        with self.assertRaisesRegex(ValueError,'inventory mismatch'): bundle.validate_archive(path)

    def test_rejects_bad_model_revision_without_weights(self):
        with self.assertRaisesRegex(ValueError,'model metadata identity'):
            bundle.metadata_integrity(json.dumps({'repo_id':'wrong'}).encode())

    def test_extracts_verified_bytes_without_overwrite(self):
        root,result=self.sample('extract')
        destination=root/'isolated'
        bundle.extract_archive(root/result['archive_filename'],destination,result['archive_sha256'])
        self.assertEqual((destination/'scripts/example.py').read_bytes(),b'print(1)\n')
        with self.assertRaisesRegex(ValueError,'new root'):
            bundle.extract_archive(root/result['archive_filename'],destination,result['archive_sha256'])


if __name__=='__main__': unittest.main()
