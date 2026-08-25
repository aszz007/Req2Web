from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from req2web_inspector.local_data import (  # noqa: E402
    CANONICAL_FLOW_RUNS_NAME,
    GUISPECTOR_RUNTIME_NAME,
    INSPECTOR_DRAFT_RUNS_NAME,
    INSPECTOR_REPLAY_BUNDLE_NAME,
    LOCAL_DATA_ROOT_ENV,
    SEMANTIC_ASSIST_RUNS_NAME,
    canonical_flow_runs_root,
    guispector_runtime_root,
    inspector_draft_runs_root,
    inspector_release_candidate_root,
    inspector_replay_bundle_root,
    resolve_local_data_root,
    semantic_assist_runs_root,
)


class InspectorLocalDataTests(unittest.TestCase):
    def test_default_root_is_a_sibling_of_the_repository(self) -> None:
        result = resolve_local_data_root(ROOT, environment={})
        self.assertEqual(result, ROOT.parent / "Req2Web_LocalData")

    def test_environment_override_is_used(self) -> None:
        configured = ROOT / "configured-local-data"
        result = resolve_local_data_root(
            ROOT,
            environment={LOCAL_DATA_ROOT_ENV: str(configured)},
        )
        self.assertEqual(result, configured.resolve())

    def test_explicit_root_wins_over_environment(self) -> None:
        explicit = ROOT / "explicit-local-data"
        result = resolve_local_data_root(
            ROOT,
            explicit_root=explicit,
            environment={LOCAL_DATA_ROOT_ENV: str(ROOT / "ignored")},
        )
        self.assertEqual(result, explicit.resolve())

    def test_stable_artifact_names_do_not_encode_a_project_phase(self) -> None:
        replay = inspector_replay_bundle_root(ROOT, environment={})
        candidate = inspector_release_candidate_root(ROOT, environment={})

        self.assertEqual(replay.name, INSPECTOR_REPLAY_BUNDLE_NAME)
        self.assertNotIn("phase", replay.name.lower())
        self.assertEqual(candidate.name, "inspector_release_candidate_v1")

    def test_run_histories_have_separate_stable_roots(self) -> None:
        draft = inspector_draft_runs_root(ROOT, environment={})
        semantic = semantic_assist_runs_root(ROOT, environment={})
        canonical = canonical_flow_runs_root(ROOT, environment={})

        self.assertEqual(draft.name, INSPECTOR_DRAFT_RUNS_NAME)
        self.assertEqual(semantic.name, SEMANTIC_ASSIST_RUNS_NAME)
        self.assertEqual(canonical.name, CANONICAL_FLOW_RUNS_NAME)
        self.assertEqual(
            {draft.parent, semantic.parent, canonical.parent},
            {ROOT.parent / "Req2Web_LocalData" / "runs"},
        )
        self.assertEqual(len({draft, semantic, canonical}), 3)

    def test_optional_guispector_runtime_is_outside_the_repository(self) -> None:
        runtime = guispector_runtime_root(ROOT, environment={})

        self.assertEqual(runtime.name, "upstream")
        self.assertEqual(runtime.parent.name, GUISPECTOR_RUNTIME_NAME)
        self.assertEqual(runtime.parent.parent.name, "optional_tools")
        self.assertFalse(runtime.is_relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
