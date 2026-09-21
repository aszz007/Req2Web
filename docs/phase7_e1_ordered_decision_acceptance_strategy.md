# Phase 7 E1 Ordered-Decision Acceptance Strategy

Status date: 2026-09-19
Status: local strategy and historical secondary analysis only
GPU, model, SSH, or paid action: not performed

## Decision

Experiment 1 should no longer use one undifferentiated score as its only
interpretation. The preserved v18 measured result already shows a small overall
Req2Web lead: A passed 27/72 browser obligations, B passed 22/72, and C passed
14/72. That five-obligation difference is descriptive and not statistically
conclusive.

The result also shows a narrower and more coherent pattern. On the complete
ordered-decision family, Req2Web passed 16/18 obligations, direct HTML passed
8/18, and the structured one-call arm passed 5/18. Req2Web beat direct HTML on
all three cases, with per-case differences of +4, +1, and +3 obligations. The
mean difference was +2.667 out of six obligations per case. With only three
cases, the exact paired two-sided sign-flip p-value is 0.25, so this is an
exploratory bounded advantage rather than a confirmatory or significant result.

The defensible claim is therefore not that Req2Web is generally better at web
generation. The candidate claim is that its explicit state and interaction
structure may help on ordered decision workflows containing a review entry,
prerequisite gate, approval, alternate rejection, and reset without stale
success.

## Why this acceptance strategy is valid

The strategy does not select individual successful checks. It retains all
three historical cases in the ordered-decision family and all six originally
frozen browser obligations for every case:

1. correct initial state;
2. enter review;
3. approve after review;
4. block approval before review;
5. reject from review; and
6. reset after rejection without stale approval.

Every criterion has equal weight. A, B, and C use the same public requirement,
browser actions, expected visible outcomes, limits, and pass/fail rules. A
missing or rejected page receives six behavioral failures; fail-closed output
is not converted into a safety success. Unknown observations remain unknown
and form lower/upper bounds rather than passes. C remains a useful secondary
baseline, but a C contract failure does not invalidate a complete A-B
comparison.

This family was selected after seeing the broader v18 result. That fact is
recorded explicitly, so the historical 16/18 versus 8/18 comparison is a
post-hoc secondary analysis. It may motivate a new study but cannot be renamed
as a preregistered primary result.

## Historical secondary result

| Arm | Ordered-decision pass | Fail | Unknown |
|---|---:|---:|---:|
| A: Req2Web | 16/18 | 2 | 0 |
| B: direct HTML | 8/18 | 10 | 0 |
| C: one-call structured | 5/18 | 13 | 0 |

| Case | A | B | C | A-B |
|---|---:|---:|---:|---:|
| Exhibition approval | 6/6 | 2/6 | 5/6 | +4 |
| Equipment release | 5/6 | 4/6 | 0/6 | +1 |
| Translation sign-off | 5/6 | 2/6 | 0/6 | +3 |

The unchanged broad result and weaknesses must remain beside this table:

- all families combined: A 27/72, B 22/72, C 14/72;
- failure recovery: A 2/18, B 7/18, C 0/18;
- cancel/restart: A 0/18, B 1/18, C 5/18; and
- conditional branch: A 9/18, B 6/18, C 4/18.

The secondary analysis therefore describes one strength and does not hide the
other weaknesses.

## Prospective confirmation rule

A stronger paper claim requires a new holdout containing at least twelve
previously unseen ordered-decision workflows. The domains and wording may vary,
but every case must expose the same six semantic roles. Cases, requirements,
browser obligations, prompts, model profile, call limits, and arm order must be
frozen before generation. No measured result may change the cases or scoring.

The label `ordered_decision_advantage_supported` is allowed only if all of the
following hold:

- all twelve A-B case pairs and all six obligations per case are present;
- there are zero unknown rows;
- missing, rejected, or invalid deliveries stay in the denominator as six
  failures;
- the mean A-B difference is at least +1.0 passed obligation per case;
- the exact paired two-sided sign-flip p-value is at most 0.05; and
- no case is removed after generation.

If A leads only on artifact traceability while browser behavior does not meet
these conditions, the result may support an inspectable-handoff claim but not
an ordered-decision behavior advantage.

## Reporting policy

Report the following separately, without a weighted overall score:

- ordered-decision browser coverage and per-case differences;
- complete broad behavior coverage, including weak families;
- claimed-success calibration against independent browser failures;
- evidence/replay completeness; and
- calls, tokens, elapsed time, and artifact size.

Req2Web uses more model calls than the direct HTML arm, so this remains a
whole-system comparison rather than an equal-compute causal ablation. It does
not establish visual quality, RAG benefit, backend correctness, developer
productivity, or general webpage-generation superiority.

## Local artifacts

- Frozen strategy:
  `fixtures/phase7_e1_ordered_decision_acceptance_v1.json`
- Fail-closed historical scorer:
  `scripts/phase7_e1_ordered_decision_acceptance.py`
- Focused tests:
  `tests/test_phase7_e1_ordered_decision_acceptance.py`
- Generated local secondary analysis:
  `outputs/phase7_e1_ordered_decision_acceptance_v1/historical_secondary_analysis.json`
  (SHA-256
  `642e41116d4ce89c98e653c615e56141f212f69f2fe9716d946281c73a4049e9`)
- Generated readable summary:
  `outputs/phase7_e1_ordered_decision_acceptance_v1/historical_secondary_analysis.md`
  (SHA-256
  `a2b59dc49ce06032556097445d2ecb4734aab53f2378b4795de7b54ce7d8eaa8`)
