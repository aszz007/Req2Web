# Experiment Methods and Frozen Results

This document summarizes completed exploratory evidence, not new experiments.
The release preserves source and authored method definitions but excludes
historical observations, raw model outputs and private run records. Exact
private recovery records are retained separately by the owner.

## E1: ordered decision behavior

- Req2Web: shared structured F1-F4 generation, owning validators/Renderer,
  bounded recovery and traceable delivery.
- Direct HTML: generate a page directly from the same public requirement;
  no Req2Web intermediate contracts are imposed on its generation.
- Structured one-call: an external-style single structured generation,
  distinguished from Req2Web's staged chain.

All three arms receive the same case requirements and browser-action criteria.
The complete ordered-decision family retains three cases and six original
obligations per case: initial state, enter review, approve after review,
block early approval, alternative rejection, and reset without stale success.
Missing/rejected pages count as six failures; unknown is never a pass.

Frozen scores are 16/18, 8/18, and 5/18 respectively. The original broad table
remains 27/72, 22/72, and 14/72. The narrower family was analyzed post-hoc and
has only three cases; paired exact p=0.25. It suggests a bounded workflow
advantage, not significant or general generation superiority.

Req2Web uses more model calls than direct HTML, so this is a whole-system
comparison, not an equal-compute ablation. It does not establish visual
quality, retrieval benefit, backend correctness, or developer productivity.

Method sources: `fixtures/phase7_e1_ordered_decision_acceptance_v1.json`,
`scripts/phase7_e1_ordered_decision_acceptance.py`, and the retained
`docs/phase7_e1_ordered_decision_acceptance_strategy.md`.

## E2: integrated trace-relation checking

- C0: container-level integrity checking, without project relation semantics.
- C1: integrity plus selected local reference checks.
- C2: Req2Web's integrated project-aware cross-artifact trace checks.
- EVL-local: experiment-authored Epsilon rules for integrity/intra-artifact
  checks, without the missing project cross-artifact relations.
- EVL-cross: experiment-authored equivalent cross-artifact Epsilon rules.

The complete six-workflow missing-relation family uses three equal obligations
per case: detect the fault, include the exact removed relation in the target
set, and leave the paired clean control alarm-free. Frozen scores are C2 18/18,
C0/C1/EVL-local 6/18, and EVL-cross 18/18. The broad result retains C1/EVL-local
18/24 detections and 12/24 exact targets; C2/EVL-cross each achieve 24/24 for
both, with zero clean alarms across six controls.

The six-case post-hoc paired value p=0.03125 is exploratory. Equivalent
external rules restore parity, so the claim is native integration of trace
semantics, not unique rule expressiveness or an out-of-the-box product
benchmark. Later Agent-navigation development failures remain negative
diagnostic evidence and are not relabeled as successes.

Method sources: `fixtures/phase7_e2_integrated_trace_acceptance_v1.json`,
`scripts/phase7_e2_integrated_trace_acceptance.py`, and the retained
`docs/phase7_e2_integrated_trace_acceptance_strategy.md`.

## Scope

No new model, GPU, Epsilon, browser-quality or H1/gold experiment is performed
for repository cleanup. The public startup corpus cannot reproduce these
scores. Prospective confirmation would need new unseen cases and criteria
frozen before observing results.
