# Experiment 2 revision: diagnostic assistance from traceability

Date: 15 September 2026. Status: design and local feasibility assessment only.
No new diagnostic-agent inference, mutation batch or measured run has occurred.
This is not a results-selected replacement for the retained E2 experiments.

## Objective and corrected claim

The owner requested problems that expose complementary capabilities of external
agents and Req2Web, with a stronger test of Req2Web's traceability benefit.
Interpret this as bidirectional coverage, not a requirement that Req2Web win.

The main question is:

> Does a provenance-preserving Req2Web trace index help the same external
> diagnostic agent identify affected entities and evidence relationships under
> a fixed investigation budget, compared with the original logs and artifacts?

This replaces the unproductive question of whether Req2Web can outperform a
general-purpose engine given the same hand-written constraints. Preserve the
actual Epsilon tie: configured EVL-cross and Req2Web each detected 24/24 of the
previous supported defects with target presence 24/24 and 0/6 clean alarms.
Do not remove this result from the paper because it is not a win.

Separately report external-tool strengths on runtime failures and limitations
of Req2Web's artifact checker. An artifact checker returning a clean artifact
verdict does not establish that the entire execution was error-free.

## Feasibility boundary from current code

The current fault checker exposes native artifact locations and identifiers.
Its acceptance checks include identity bindings and package/render mirrors.
These are suitable inputs for entity- and artifact-level investigation.

The existing `build_inspector_element_acceptance_trace` API requires all G0
identity artifacts, including a browser report, obligation/decision artifacts
and acceptance evaluation. The old E2 bundles do not supply that complete
chain. The API also preserves G1/G2 unavailability instead of mixing them with
G0 artifacts. Consequently, this study must not invent a complete
requirement-to-browser-outcome trace or import protected H1/gold to fill gaps.

Start with verifiable artifact, decision, entity, field and use-case references
actually present in the approved packet. A missing or ambiguous link stays
missing or ambiguous. Requirement impact is scored only for explicit mappings
with independently established task obligations; shared retrieval evidence
does not by itself establish a use-case relationship or satisfaction.

Code inspected: `src/req2web_faults/detector.py`,
`src/req2web_inspector/trace.py`, and the pinned AgentDebugX core rule source.
AgentDebugX's deterministic mode includes explicit-error and repeated-action
checks. These are runtime coverage probes, not substitutes for a reasoning
agent capable of examining a trace index.

## Small balanced dataset

Create four new project-authored English workflows with similar ambiguity:
two multi-use-case forms, one search/detail workflow, and one review workflow.
Do not use the already scored six cases as held-out evidence.

Each workflow contributes five packets: three structural defects, one runtime
defect, and one clean control. Total: **20 packets**, including 12 structural
packets, four runtime packets and four clean controls. Cases, not mutations,
are the four independent clusters; report individual cases and paired counts,
not unsupported population claims or significance from twenty independent rows.

| Family | Intervention and question | Why it is informative | Boundary |
| --- | --- | --- | --- |
| S1: valid-ID wrong relation | Redirect a relation to a different existing entity/use case; preserve syntax and local IDs. Which relation and downstream entity are inconsistent with the trusted source? | Tests relationship meaning rather than missing-string detection | No assertion that Req2Web already supports this fault; missed/unsupported outcomes remain |
| S2: stale cross-stage artifact | Mix a valid older artifact with a newer same-workflow artifact, preserving each local file's integrity record. Identify the inconsistent stage boundary and affected objects. | Tests source/run/version binding rather than a corrupted byte checksum alone | An independent trusted source/version receipt must exist; coherent replacement without such a receipt can be unidentifiable |
| S3: two independent defects plus irrelevant context | Inject two independently recorded defects into different artifacts; retain unrelated successful steps. Identify both affected targets and distinguish their supporting evidence. | Tests completeness and false attribution; a single easy alarm is insufficient | Report a set of injected origins, not a fabricated single causal root |
| R1: explicit tool failure, two workflows | A local synthetic tool invocation really raises a recorded timeout/parameter exception while the artifact package remains valid | Positive coverage control for runtime-oriented debugging; may be outside the artifact checker | No actual paid API/network failure is induced; label as deterministic injected-runtime evidence |
| R2: repeated no-progress invocation, two workflows | A bounded local fixture really records repeated calls with unchanged relevant state | Tests runtime sequence diagnosis, which static artifact integrity does not establish | Use the pinned tool's documented/default rule setting, frozen before cases; include legitimate repeated activity in controls |
| C: clean controls | Consistent artifacts and successful execution; include non-error warnings or legitimate repetition without stating that failure occurred | Tests whether an agent invents causes from suggestive words or extra context | Clean traces must be operationally consistent, not sanitized to favor a keyword detector |

S1-S3 are hypotheses about useful discriminating tasks, not assumed wins.
If all artifact versions and semantic references are changed coherently, a
trusted external obligation may be necessary to recognize an error. Without
that information, the gold label must be `not_identifiable`, not an enforced
answer that a tool is penalized for failing to guess.

Synthetic runtime recording uses actual local fixture calls and their observed
order/output. Do not turn static post-injection snapshots into invented F1-F4
events. If an earlier gate blocks execution, preserve that termination; do
not forge downstream events to construct a cascade.

## Conditions: isolate traceability, not the model

### Primary paired conditions

- **A: raw evidence.** One fixed external reasoning agent receives the task,
  inventory, neutral runtime events, schema/field documentation and read-only
  access to every allowed raw artifact.
- **B: raw evidence plus trace index.** The exact same agent, prompt, decoding
  configuration, tools and budget receive all of A plus a Req2Web-derived
  navigation index of source-grounded entities and relationships.

The index contains only facts recoverable from A's allowed artifacts. Each
node/edge cites its source file hash and JSON pointer or DOM attribute. It
must contain no injected-fault labels, expected answers, computed diagnostic
verdicts, suggested root cause, private baseline package or scorer-only data.
Do not give B C2 diagnostics while withholding them from A: that would measure
additional diagnosis, not merely traceability. Either withhold those reports
from both arms or create a separately named future condition.

The index itself is a material intervention. If a new experiment-only index
must be implemented, call it that; do not claim the production Inspector
already exposes an accepted capability that has not been integrated.
An index-generation error is recorded as an intervention failure, not silently
fixed after a measured response or replaced with an oracle-generated index.

### Secondary CPU-only coverage conditions

Keep the existing Req2Web artifact checker and pinned AgentDebugX heuristic
mode as separate native-capability references. Feed them compatible projections
of the same packet, with a published input coverage table. Unsupported input
is `unsupported_input`, not a detection miss. Artifact-only applicability and
whole-run diagnosis remain separate denominators.

The previously executed Epsilon equivalent-rule experiment is an external
correctness reference. No further rule tuning or Epsilon rerun is necessary
for the primary paired assistance question.

## Equal evidence and bounded agent budget

Freeze one model/version and one diagnostic-agent implementation before
measurement. Do not select a model after observing which produces the largest
A/B gap. No provider, model profile or paid resource is selected by this plan.

- Twenty packets x two arms = **40 measured diagnostic sessions**.
- At most six read-only artifact/index reads per session. Index access consumes
  the same budget as any other read; no hidden free retrieval.
- At most 12,000 total input tokens and 2,048 final-output tokens per session.
  If a packet cannot be investigated within these limits in development,
  amend both arms' common budget before freeze; no silent truncation.
- Bound each session to 120 seconds and at most seven provider turns, including
  the final answer. Measured aggregate cap: 280 provider turns. Tokens and
  actual invocations are separately recorded; a session is not necessarily
  one model call.
- No retry, repair, critique-and-revise cycle, cross-session memory or reuse of
  the other arm's answer. A timed-out/invalid answer remains a terminal result.
- Alternate A/B execution order by case, with a frozen seed/order manifest.
  Run sessions independently; no case label or arm label hints about quality.
- At most two unrelated development packets, both arms once: four development
  sessions, at most 28 provider turns, separately budgeted and never pooled
  with the measured forty sessions.

These are proposed caps, not runtime authorization or a claim that inference
has occurred. Use an already owner-approved suitable runtime only after an
action-time model/provider and cost decision. Do not consume E1's 60-call
budget, modify its prompts or add diagnosis calls to its frozen run.

## Common output contract and scoring

Require the same answer fields in both paired conditions:

```json
{
  "status": "fault | no_fault | unknown | unsupported_input",
  "origin_candidates": [{"artifact": "...", "entity": "...", "field": "..."}],
  "affected_use_cases": [],
  "evidence_edges": [{"from": "...", "to": "...", "source": "...", "pointer": "..."}],
  "uncertainty": "..."
}
```

The schema above is illustrative until the exact parser contract is frozen;
the vertical-bar strings denote alternatives, not literal schema enums.

Primary metrics, fixed before measured responses:

1. **Origin-set precision/recall:** exact artifact/entity/field matches against
   independently recorded injected origins. In S3, finding one of two targets
   earns partial recall, not complete localization.
2. **Evidence-edge precision/recall:** returned links must exist in the frozen
   source or be explicitly supported contradiction claims with two cited
   endpoints. Enumerate all acceptable equivalent short paths before scoring;
   do not force a proprietary graph notation or a single preferred path.
3. **Complete supported diagnosis:** required origin set and supported path
   recovered, without unsupported origin claims. Merely copying every ID
   must lose precision, not become a perfect diagnosis.

Secondary metrics: affected-use-case set precision/recall where an explicit
mapping exists; clean false alarms; unsupported/unknown/invalid-answer rates;
reads, input/output tokens, provider turns and elapsed time within the common
environment. Full-chain browser/semantic correctness is not scored without
the corresponding independently available artifacts.

Report results by family and case and as paired outcomes: both succeed,
only A succeeds, only B succeeds, neither succeeds. The primary intention-to-
evaluate table keeps all assigned sessions, including failures/timeouts.
Separate native-tool applicability tables prevent unsupported formats from
being reclassified as either successful diagnosis or an accuracy miss.

## Gold, blinding and stop conditions

Record mutations and valid alternative answers before running any diagnostic
condition. Do not derive gold from C2/EVL findings or the test agent's answer.
Keep mutation records outside the read-only tool root. Source documents and
receipts establishing version/obligation truth must be available to both arms;
scorer-only information may define injected origins but may not be necessary
hidden evidence for identifying them.

Review the four templates and applicability before authoring instances. Keep
all preselected families even if Req2Web misses them. Check development-case
adapter/schema correctness once, fix affected interface issues only, then
freeze source/configuration/input/gold identities. Measured runtime failure
ends that session; retain partial bytes and move to the next scheduled session
without automatic retries. Never regenerate a measured case to obtain a win.

Stop before the measured batch if the index invents edges, packet chronology
is fabricated, a target is unidentifiable from shared evidence, or model/cost
authority is missing. Lack of a positive A/B effect is a valid outcome, not
a trigger to change the prompt, cases or metrics and repeat the experiment.

## Execution handoff

1. Manager: approve template/fault coverage and verify native trace availability.
2. Local implementation: create isolated case and read-only evidence/index
   adapters; preserve all original E2/Epsilon evidence and E1 files. No new
   production authority, full-flow variant or protected H1 access.
3. One focused local check: projection fidelity, unavailable-link handling,
   neutral source citations, no label reads and response-accounting limits.
4. Select/approve the diagnostic runtime and cost limits; perform only the
   separately capped development sessions, then freeze exact measured inputs.
5. Execute the forty measured sessions once and score their raw responses.
6. Publish the full paired table, including external strengths and Req2Web
   blind spots; update paper claims to the actual observed effect.

Current completion: design, pinned-source capability inspection and feasibility
boundary assessment. New fixtures, trace-index adapter, diagnostic runtime and
measured results are not yet completed. Do not label this document as an
executed experiment or use its hypotheses as numerical paper results.

## Superseding local-preparation status (2026-09-16)

The owner subsequently authorized local code preparation. Four measured task
templates and two separate development templates now produce 22 public packets,
40 scheduled measured sessions and four scheduled development sessions. The
experiment-only source-occurrence index, paired diagnostic runtime, raw-bound
scorer, Linux watchdog and separate upload candidate are implemented. No
diagnostic model session has executed; model/runtime selection and server
action remain pending. See `phase7_e2_diagnostic_runbook.md` for the handoff.

This narrows the primary experiment to equal-evidence index-assisted diagnosis.
The public structural projections are not native Inspector or AgentDebugX
inputs; native-tool coverage adaptation remains deferred. Render labels are
copied specification metadata, not browser-observed text. Local fixture calls
provide real recorded synthetic errors/no-progress, not invented F1-F4 model
trajectories. The new index is not an existing production capability, and
neither these fixtures nor mocked checks establish an A/B effect. Historical
Epsilon results and the frozen E1 preparation are unchanged.
