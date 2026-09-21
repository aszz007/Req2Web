# E1 postmortem and proposed workflow-prototype comparison

Date: 2026-09-17. Status: the first frozen v4 development run is closed and its
measured split was blocked. The successor v5 harness, frozen inputs and server
payload are prepared locally; no v5 model inference has been executed.
This document supersedes the proposed interpretation of E1 v1, not its raw
results. It is a prospective, post-hoc-informed development design, not a
retroactive preregistration or an authorization to start a paid run.

## Decision

Do not rerun the old comparison unchanged. Preserve the observed 0/48 versus
38/48, but do not treat it as an independently blinded, fully audited estimate
of strict functional effectiveness. Public sample values are present in both
arms; source-level input omission was suspected and then disproved by actual
returned inputs. Fix the generation-to-rendering
contract before spending GPU time. Evaluate the intended product as a
verifiable interactive workflow prototype, with a direct-code reference and a
single-generation structured baseline. Keep actual browser behavior primary.

The desired advantage is testable, not assumed: explicit structure may make
workflow constraints and delivered behavior easier to check and reproduce.
It does not imply better arbitrary application generation, visual quality,
retrieval use, user productivity, or automatic root-cause diagnosis.

## Verified failure mechanism

Evidence: `outputs/phase7_experiment1_v2_design/postmortem-20260917-verified.json`.
The earlier `postmortem-20260917.json` is superseded for input-visibility
interpretation: it inspected unexpanded fixture text rather than actual input.
Reproduce the bounded static audit with:

```powershell
.venv\Scripts\python.exe scripts/phase7_e1_postmortem_audit.py `
  --result-root outputs/phase7_experiment1_v1/returned-corrected-20260916-41450/extracted `
  --output outputs/phase7_experiment1_v2_design/postmortem-new.json
```

The audit verifies every asset named by the returned 24-artifact inventory,
reads the delivered PageSpecs and HTML, and binds current inspected source
hashes separately. It neither regenerates pages nor changes browser verdicts.

### 1. Accepted component names are not executable renderer capabilities

The ten model-delivered pages contain 52 PageSpec components. All 52 were
rendered as `data-renderer-kind="fallback"`. All ten pages contain zero
`input`, `select`, or `textarea` elements. The other two pages are separately
recorded deterministic G0 deliveries after F2 contract failures.

For example, expense entry generated `text_input`, `number_input`,
`submit_button`, `error_display`, and `success_display`. These convey the
intended roles, but none is supported by the current renderer's exact names.
The model therefore did not simply ignore the request for fields: its
component descriptions were accepted upstream and lost executable meaning
at the rendering boundary.

Source evidence:

- `src/req2web_orchestration/phase4_graph.py`, `validate_f1_output`: component
  type is validated as nonempty text, not renderer-supported semantics.
- `src/req2web_generation/renderer.py`, `SUPPORTED_COMPONENT_TYPES` and
  `_render_component`: seven exact types; unknown types become generic
  explanatory content and, where applicable, generic trigger buttons.
- `src/req2web_generation/consistency.py`: unsupported component rendering
  produces a capability warning. Preserving the declared type and rendering
  its generic fallback can satisfy structural conformance.

Thus the gates checked their current contracts, but those contracts do not
establish executable fulfillment of the public task. Calling this complete
functional success would be incorrect.

### 2. Correcting names alone cannot implement the old tasks

`ComponentSpec` contains identity, section, type, label and purpose; it has no
typed field constraints, options, collection rows or value binding.
`InteractionSpec` carries source, target, trigger and descriptive action/
feedback, but no executable data predicate or record update.

The renderer's `form` contains one generic required text input. Its `data_view`
hardcodes 24 active items and 88% completion. Search does not compute filtered
records. JavaScript changes visibility and feedback, not task data. These
cannot implement positive-amount validation, filtering four records, or
updating only selected records simply by renaming a component.

Additional source-level risks require targeted checks before a new workflow
claim: `runForComponent` falls back to a trigger's first interaction when no
source-state match exists; the empty-required-input branch still dispatches
the transition. These are code findings, not newly measured failure counts.

### 3. Input omission was disproved; upstream abstraction still has errors

Although `store.create` has no separate `sample_data` argument,
`model_visible_case` appends canonical public sample JSON to the requirement
before both preparation and execution. All twelve actual returned A raw
inputs and all twelve canonical B artifacts exactly contain that expanded
public requirement. B gets the same expanded requirement plus a duplicate
structured data field. There is no established missing-sample-information
confound. Future comparisons should remove redundant presentation differences,
but must not explain away the negative result as absent public input.

The canonical use cases have a different problem: the parcel task's returned
B includes "Manage the cart and complete checkout". `understanding.py` uses
substring keyword matching and maps `order` to checkout; the requirement uses
"ordered workflow". Some other tasks receive generic use cases. This is a
semantic abstraction error despite preservation of the complete original
requirement. It can contaminate mapping and acceptance scope, but its isolated
effect on the zero score has not been measured.

The current F3 prompt/required plan also imposes self-loops plus adjacent
forward transitions in F2 array order and excludes backward transitions.
That contract cannot faithfully express arbitrary branching, Back, reset or
reopen workflows. A model can satisfy it without implementing the user's
intended graph. The successor population below therefore needs an explicit
shared planner/contract revision, not just a renderer type-name fix.

### 4. The observer overstates some evidence

- Alias-only records are not independent blinding. The binding source uses
  `_A` for all generic Req2Web pages, the reviewer had source access, and
  branding/layout reveal the method. Call this author/agent observation.
- `approve_only_after_review` checks the normal review-then-approve path,
  without trying to approve before review.
- `complete_assigned_task` checks for "Completed" after assignment without
  executing the separately named completion action.
- Preservation-after-failure checks can pass from unchanged inputs even if
  the required failure was never observed.
- Required-field attributes alone do not prove each field blocks submission.

Therefore 38/48 is the historical scorer output, not a fully audited estimate
of strict requirement satisfaction. Preserve it with qualifications rather
than replacing it with a guessed lower score. The observed missing controls
in A remain substantive despite these evaluator limitations. The bootstrap
interval quantifies resampling of these labels; it cannot correct observation
bias or a shared renderer failure.

### 5. What the high direct-HTML result actually means

B can emit arbitrary HTML, JavaScript, fields and data operations in one
response; A must pass through a much narrower intermediate representation
and renderer. Small offline tasks can suit direct generation; both arms had
the complete public sample records. Its observed advantage is plausible, but
the exact magnitude is limited by the observer weaknesses. A used 44 calls versus B's 12, so this was
not equal compute either. No result here identifies a causal RAG benefit:
the measured model route had an empty provider-visible retrieval payload.

Earlier 12/12 browser/PageSpec checks answered whether a delivered page could
execute its own declared transitions. They do not imply that all independent
business requirements were implemented. Previous broad summaries of those
checks must retain that distinction.

## Research questions and claims

| Question | Evidence needed | Possible Req2Web advantage |
| --- | --- | --- |
| RQ1: Does the prototype perform the requested workflow? | Independent visible actions, legal and illegal paths | Fewer missed transitions or invalid shortcuts; not guaranteed |
| RQ2: Does a claimed successful delivery deserve that status? | Delivery status versus independent behavioral outcomes | Fewer false success claims while retaining useful delivery yield |
| RQ3: Can the same checked artifact be reproduced and its behavior explained? | Exact asset identity, repeatable browser traces, validated links | Structured, inspectable handoff; functional truth still checked independently |

E2 retains fault localization and diagnostic-agent effort. E1 does not add a
second diagnostic-agent study, count trace-file existence as functionality,
or treat generic provenance as a unique invention. Existing Epsilon equal-rule
agreement remains a tie.

The literature supports matching evaluation to the claim.
[WebGen-Bench](https://arxiv.org/abs/2505.03733) pairs website operations with expected
outcomes. [Design2Code](https://arxiv.org/abs/2403.03163) primarily evaluates
screenshot-conditioned visual reconstruction.
[W3C PROV-O](https://www.w3.org/TR/prov-o/) already defines provenance relationships.
These motivate separate behavior, presentation and provenance evidence; they
do not establish Req2Web's superiority. Their published scores are not
comparable with the twelve local synthetic tasks.

## Implemented local prerequisites: minimum bounded correction

These corrections are implemented in the local E1 v2 candidate and verified
without a model. They remain development changes, not new effectiveness
evidence. The measured run must use the frozen payload without live edits.

1. Define one shared executable capability registry. F1 contracts and the
   renderer must agree on exact supported component types. Unknown types may
   remain inspectable, but cannot count as executable task delivery.
2. Correct semantic planning before expanding scope: prevent keyword-substring
   matches such as "ordered" becoming a checkout use case, and revise the
   shared F3 contract/planner beyond compulsory adjacent-forward transitions
   to support the specified branches, Back, cancellation/reset and reopen.
   Keep one owning planner and matching validators, not an experiment-only
   alternate product flow. Then make runtime transitions source-state exact:
   disable/reject unavailable actions and remove fallback dispatch to another
   source state's first transition. Invalid input must not advance to success.
3. Gate unsupported task capabilities explicitly. Distinguish structure-valid,
   executable-prototype, rejected, repaired and fallback delivery. Rejection
   is not functional success, and fallback is tested under the same tasks.
4. Retain and verify the existing shared expanded public task string, including
   every record value if used. Remove B's redundant separate sample-data copy
   in the successor and compare exact submitted input artifacts, not merely
   the call-site argument names. Avoid a second upstream flow.
5. Correct the experiment observer: positive and negative controls, failure
   prerequisite, record-level assertions where relevant, error capture and
   full asset hashes. Keep independent assertions out of generator prompts.

Do not extend PageSpec into a complete application data language just to win
this iteration. Typed numeric/email/date fields, collection filtering,
per-record updates, persistence and dynamic interpolation are a separate
larger implementation if arbitrary offline applications remain a paper claim.
That extension would require a versioned contract and additional experiments.

## Prospective E1 v2 population

Scope: small offline workflow prototypes with explicit buttons, visible states,
branching, cancellation and simulated recovery. Backend correctness,
multi-record CRUD, visual fidelity and arbitrary business data validation are
outside this population. This is a narrower estimand than E1 v1 and must be
stated in the paper, including the fact that it was selected after the v1
failure. It is not a replacement score for general webpage generation.

Use four task families, three new measured tasks per family, plus one separate
development task per family. Each task has six independent obligations.
All states/actions and outcomes must appear in the public natural-language
requirement; private test selectors, scripts and verdicts do not.

| Family | Measured tasks | Separate development task | Six required behaviors |
| --- | --- | --- | --- |
| Ordered decision | exhibition approval; equipment release; translation sign-off | document approval | correct initial state; enter review; approve after review; block pre-review approval; reject from review; reset after rejection without stale success |
| Explicit failure and recovery | mock archive transfer; report export; configuration deployment | sample download | ready state; first attempt fails; no success on failure; retry reaches success; retry unavailable before failure; terminal repeat does not restart or duplicate completion |
| Cancel and restart | onboarding walkthrough; publishing wizard; inspection checklist | setup walkthrough | start step; advance to second step; back returns to first; cancel resets; restart exposes no stale completion; confirmation only after all mandatory steps |
| Conditional branch | basic/extended review; local/remote handoff simulation; standard/exception approval | short/long walkthrough | initial branch choice; first branch path; second branch path; no shortcut to terminal; alternative actions unavailable on current branch; reset permits the other branch |

No data-dependent branching is implied: branches use explicit user choices.
Each measured task is a distinct workflow within its family, with exact
wording and assertions frozen before generation. Naming variants alone are
not independent real-world tasks; report family results and the small-sample
limitation. The table is the design specification, not a frozen input packet.

Development may change code and prompts. Measured outputs may not be used to
change either, select tasks, enlarge budgets or regenerate. Any materially
different successor is a separately named study with the old result retained.

## Three comparison arms

| Arm | Generation | Shared elements | Purpose |
| --- | --- | --- | --- |
| A: Req2Web | Canonical F1-F4, one call per stage, existing deterministic policy | Public task, pinned model, declared capabilities | Whole implemented system |
| B: direct HTML | One complete offline HTML/CSS/JS response | Same public task and independent browser tests | Practical unconstrained reference |
| C: single structured generation | One response containing the semantic content required for a PageSpec | Same canonical B/context projection as A where admissible, same registered IDs/assembler, renderer and final gates | Test whether intermediate representation alone explains the result |

C is an experiment-only baseline, never a second active product runtime.
Its adapter may deterministically register references and use the owning
assembler; it must not invent missing workflow semantics or use evaluation
answers. Accept/reject raw output under a frozen contract, preserve failures,
and allow no model retries. C passed a model-free representability check before
the v2 input freeze. This does not predict whether its one real model response
will satisfy the contract.

A versus C compares incremental generation/checking with single generation
under a shared representation, but call budgets still differ. It does not
isolate an equal-compute architectural causal effect. A versus B measures the
whole-system tradeoff. If efficiency or superiority over iterative coding
agents becomes a claim, a separately preregistered equal-budget iterative-HTML
baseline is required; the one-shot comparison cannot establish that claim.

Keep model revision/BF16/greedy/non-thinking decoding fixed. Retain A's
node-owned token caps; provision B and C at 8192 output tokens each only if
their development contracts fit, then freeze. Publish input/output tokens,
generation time, cold-load time and total wall time separately. Retrieval
content is either identically absent, as in v1, or identically supplied under
an accepted route revision; do not call this a RAG ablation.

## Independent observation and metrics

Before any measured output, freeze natural-language obligations and test
actions/expected outcomes from the public task, not generated F4 or PageSpec.
Selectors may be bound afterward using visible roles/labels only. Freeze the
selector map before observation and retain the binder identity. Do not claim
true blinding when branding or access to source reveals the method.

Use a fresh context per obligation, at most 16 visible actions and 90 seconds,
with console/page-error capture, before/after state evidence and screenshots.
Six obligations x twelve cases x three arms = 216 measured observations.
The increase from twelve to sixteen actions accommodates setup plus explicit
negative paths, equally for all arms. Count every click, input, selection and
reload. Reading DOM properties is allowed; injecting application state or
calling internal transition methods is not.

For negative assertions, try the forbidden visible action if present. If
unavailable, verify it is absent or disabled and show the allowed path still
works. Success text alone is insufficient. Failure-preservation assertions
first require the failure event; action-specific checks must execute that
action. Stop at the frozen limits and retain unknowns.

Report these columns separately, without a weighted "overall advantage" score:

1. **Primary: independent behavior coverage.** Passed obligations / 72 per
   arm, plus per-case /6, all-six success /12 and family breakdowns. Missing
   pages and rejected tasks contribute zero passes; infrastructure unknowns
   contribute lower/upper bounds, not automatic passes.
2. **Delivery calibration.** Map native status before execution to claimed
   success, explicit limitation/rejection, fallback or no artifact. Among
   claimed successful deliveries, count any with an independent behavior
   failure. Show the denominator and unconditional successful-task yield, so
   rejecting everything cannot look best. HTML emission alone is not an
   explicit success claim: score B with a common external check and report its
   native status as undeclared if appropriate. Separate native and common
   checks instead of pretending all arms have an identical success signal.
3. **Common verification and replay.** Check every delivered artifact under the
   same external assertions. On three cases chosen in advance (exhibition
   approval, report export, publishing wizard), perform exactly one additional
   fresh-context replay per arm without generation. Compare visible outcome
   traces; exclude this replay from primary denominators. Nine repeated page
   sessions, six obligations each, create 54 supplementary observations.
4. **Handoff evidence.** Audit requirement-to-control/transition/test links for
   A and C: resolve each link to the actual frozen artifact and observation.
   Record B's native availability and common harness evidence, without
   penalizing B's behavior for lacking Req2Web IDs. Link existence is not
   semantic correctness; no cross-format "trace accuracy" ranking here.
5. **Resources.** Calls, tokens, durations and artifact size; no invented
   monetary price and no "more efficient" claim from unequal budget runs.

For primary differences report A-B and A-C per task, with all twelve paired
rows. An optional fixed-seed paired case bootstrap is descriptive for these
authored tasks only; explicitly acknowledge four family clusters and no
population-level generalization. Do not treat 216 obligations as independent
subjects. No statistical noninferiority claim without a justified margin and
adequate sample size.

Interpretation rules are fixed before measured generation:

- A beats B and C on independent behavior: supports a bounded system benefit,
  subject to differing generation budgets and the narrow task population.
- A and C tie above B: supports the structured representation/shared execution
  approach; does not prove F1-F4 is necessary.
- A and B tie but A supplies correct replayable links: demonstrates an
  inspectable handoff at the reported cost, not superior task completion or
  reduced developer time.
- B wins again: report that outcome, narrow the tool claim and fix the product
  if stronger functional claims are still intended.
- All methods score near perfectly: a ceiling result, not proof of advantage.
  Do not add harder measured tasks until the winner changes.

## Execution order and bounded budget

1. Complete the five local prerequisites. Use the four development workflows
   as authored reference fixtures to check representability without a model;
   these are harness/product checks, not experimental successes.
2. Validate the observer against a working page and deliberately broken
   variants: missing action, illegal shortcut, inert success prose,
   nonexistent failure with preserved inputs, stale terminal state. Use these
   only to validate the evaluator, not as favorable measured cases.
3. Freeze all inputs, source, prompts, schemas, selectors procedure, decoding,
   budgets and schedule. Confirm identical public-task bytes and no private
   test/gold leakage. Validate full asset binding and the C adapter once.
4. When the owner starts a server, run four development cases across A/B/C:
   at most 24 model calls. The predetermined gate requires actual rendering
   capability, bounded terminal accounting and operable observation, not an A
   win. Definitive fail-closed model-product outcomes count as zero rather than
   disappearing from the denominator. They are eligible only when a model call
   started, the row exited 2, the immutable inventory is complete and all six
   observer rows are definitive failures. Unstarted, timed-out, interrupted,
   ambiguous, unknown or inventory-invalid outcomes stop the measured batch.
   Ordinary model behavioral mistakes are recorded, not repaired into passes.
5. Only after the same-freeze readiness gate, run twelve measured cases across
   A/B/C: at most 72 model calls. Use one attempt per row, zero automatic retry,
   balanced arm order, a supervising parent and durable partial output. Keep
   the existing per-call ceiling of 1200 seconds and a six-hour global cap.
   Budget exhaustion or interruption ends the attempt, with unstarted rows
   explicit. Do not regenerate successful or failed rows.
6. Return all artifacts and stop GPU use. Perform 216 primary plus 54
   supplementary replay observations locally; no GPU is needed for scoring.
   Reuse unchanged validation evidence. Human review may adjudicate recorded
   evidence but cannot silently alter assertions or overwrite raw labels.
7. Report the complete table, explanations, resource tradeoffs and boundaries.
   E2 proceeds on its own plan. Record the demo only after its shown path has
   corresponding valid behavior evidence.

The v4 development run used 18 calls and is historical evidence, not part of a
v5 continuation. The v5 ceiling remains 96 fresh calls, of which at most 24 are
development and 72 are measured. This is a ceiling rather than a duration
estimate. V5 local readiness is complete; its fixed-constant transport adapter
does not select, delete or invent workflow semantics, and semantic conflicts
continue to fail closed.

## Paper positioning

Current defensible wording: "Req2Web exposes structured intermediate artifacts,
cross-stage identities, delivery decisions and replayable evidence for offline
prototypes. A development comparison exposed a mismatch between accepted
component contracts and executable rendering, motivating an explicit
capability boundary and independent workflow evaluation."

Potential wording after supportive new evidence: "Within the evaluated
workflow-prototype scope, Req2Web provides [measured behavior result] together
with [verified handoff evidence], at [measured resource cost]."

Do not say that the current system generates arbitrary functioning applications,
always rejects semantic errors, outperforms external agents, uses retrieved
evidence causally, or improves developer productivity. Those require different
evidence. This work establishes neither H1 nor formal-quality closure.
