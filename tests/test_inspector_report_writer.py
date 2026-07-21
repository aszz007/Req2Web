from __future__ import annotations

from pathlib import Path
import json
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from req2web_acceptance import (
    compile_acceptance_binding,
    compile_acceptance_plan,
    execute_acceptance_binding_plan,
    project_requirement_view,
)
from req2web_evaluation import evaluate_acceptance, normalize_candidate_decisions, normalize_gold_obligations
from req2web_generation import (
    DeterministicPageRenderer,
    PageSpecBuilder,
    RetrievalGuidanceBuilder,
    RetrievalGuidedPageSpecBuilder,
    RetrievalInfluenceChecker,
)
from req2web_inspector import (
    InspectorTraceWriterError,
    build_inspector_element_acceptance_trace,
    project_g0_inspector_facts,
    project_model_inspector_facts,
    write_inspector_trace_report,
)
from req2web_rag.corpus import ROLE_ORDER
from test_acceptance_browser_executor import FakeBrowserBackend
from test_guided_page_spec import build_context


class InspectorTraceReportWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / ".tmp_inspector_report_writer" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        context = build_context()
        guidance = RetrievalGuidanceBuilder().build(context)
        builder = RetrievalGuidedPageSpecBuilder()
        guided = builder.build(context, guidance)
        ablations = {role: builder.build(context, guidance, disabled_roles=(role,)) for role in ROLE_ORDER}
        render = DeterministicPageRenderer().render(guided.page_spec, self.root / "page")
        influence = RetrievalInfluenceChecker().check(context, guidance, PageSpecBuilder().build(context), guided, ablations, render)
        view = project_requirement_view(context)
        plan = compile_acceptance_plan(view)
        binding = compile_acceptance_binding(view, plan, guided.page_spec, render)
        browser = execute_acceptance_binding_plan(binding, render.index_html.resolve().as_uri(), backend=FakeBrowserBackend(binding))
        gold = normalize_gold_obligations("development-report-writer", plan)
        candidate = normalize_candidate_decisions("development-report-writer", guided.page_spec)
        evaluation = evaluate_acceptance("development-report-writer", plan, binding, browser, guided.page_spec, gold, candidate)
        facts = project_g0_inspector_facts(guidance, guided, guided.page_spec, influence)
        self.trace = build_inspector_element_acceptance_trace(
            facts, guidance=guidance, guided_build_result=guided, page_spec=guided.page_spec,
            retrieval_influence_report=influence, requirement_view=view, acceptance_plan=plan,
            render_result=render, binding_plan=binding, browser_report=browser,
            gold_obligations=gold, candidate_decisions=candidate,
            acceptance_evaluation_report=evaluation,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.parent.exists() and not any(self.root.parent.iterdir()):
            self.root.parent.rmdir()

    def test_fixed_static_files_are_byte_stable_and_manifest_hashes_recompute(self) -> None:
        first = write_inspector_trace_report(self.trace, self.root / "first")
        second = write_inspector_trace_report(self.trace, self.root / "second")
        self.assertEqual(Path(first.json_path).read_bytes(), Path(second.json_path).read_bytes())
        self.assertEqual(Path(first.markdown_path).read_bytes(), Path(second.markdown_path).read_bytes())
        self.assertEqual(Path(first.manifest_path).read_bytes(), Path(second.manifest_path).read_bytes())
        manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
        for item in manifest["files"]:
            payload = (Path(first.output_dir) / item["file_name"]).read_bytes()
            self.assertEqual(len(payload), item["byte_size"])
            from hashlib import sha256
            self.assertEqual(sha256(payload).hexdigest(), item["sha256"])
        markdown = Path(first.markdown_path).read_text(encoding="utf-8")
        for marker in ("## Retrieval Influence", "## Sources", "## Element Traces", "## Complete Acceptance Status", "## Structural Gaps"):
            self.assertIn(marker, markdown)
        self.assertIn("structural identity only", markdown)

    def test_writer_does_not_mutate_trace_and_rejects_unsafe_or_nonempty_paths(self) -> None:
        before = self.trace.canonical_json_bytes()
        write_inspector_trace_report(self.trace, self.root / "safe")
        self.assertEqual(before, self.trace.canonical_json_bytes())
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(InspectorTraceWriterError):
            write_inspector_trace_report(self.trace, occupied)
        with self.assertRaises(InspectorTraceWriterError):
            write_inspector_trace_report(self.trace, self.root / "nested" / ".." / "escape")

    def test_unavailable_trace_writes_no_model_facts_or_external_assets(self) -> None:
        unavailable = build_inspector_element_acceptance_trace(project_model_inspector_facts("G2", provider_log="untrusted"))
        result = write_inspector_trace_report(unavailable, self.root / "unavailable")
        markdown = Path(result.markdown_path).read_text(encoding="utf-8")
        self.assertIn("fail_closed", markdown)
        self.assertIn("Missing Artifacts", markdown)
        self.assertNotIn("http://", markdown)
        self.assertNotIn("https://", markdown)
        self.assertEqual({path.name for path in Path(result.output_dir).iterdir()}, {"inspector_trace.json", "inspector_trace.md", "inspector_trace_manifest.json"})


if __name__ == "__main__":
    unittest.main()
