# Phase 7 E2 Integrated Trace-Checking Acceptance Strategy

Status date: 2026-09-20
Status: frozen local read-only secondary analysis
Model, GPU, SSH, paid action, detector rerun, or external-tool rerun: not performed

## Decision

Use the complete six-case `inspector_trace_relation_removed` family as an E2
secondary acceptance result. This is analogous to the frozen E1
ordered-decision analysis: it keeps every case in one coherent capability
family and every pre-existing outcome, rather than selecting individual
successes.

The capability under test is integrated cross-artifact trace checking. Removing
one Inspector relation leaves each individual JSON artifact locally coherent.
A detector must reconstruct the expected relation from other Req2Web artifacts
to find and localize the fault. This directly represents the framework's
traceability value and is more appropriate than judging a general Agent on
whether it voluntarily chooses an optional lookup tool.

## Frozen cases and acceptance rule

The family contains six different project-authored workflows:

1. expense entry;
2. workshop enrollment;
3. directory filtering;
4. review workflow;
5. notice recovery; and
6. booking recovery.

Each case has one trace-relation mutant and one paired clean control. Every
condition receives one equal point for each of three criteria:

1. `trace_fault_detected`: the mutant produces an alarm;
2. `exact_trace_target_present`: the exact removed trace relation is present in
   the diagnostic targets; and
3. `paired_clean_control_has_no_alarm`: the unchanged case remains clean.

The denominator is therefore 18 per condition. Detection alone is not enough,
and a broad alarm without the exact relation fails localization. Clean behavior
is part of every case rather than a detached footnote.

## Frozen secondary result

| Condition | Trace-family acceptance | Interpretation |
|---|---:|---|
| C0: integrity checks | 6/18 | Clean controls only; no trace fault detection or target |
| C1: integrity plus local references | 6/18 | Local consistency is insufficient for a missing cross-artifact relation |
| C2: full Req2Web | 18/18 | All six faults detected and exactly localized; all clean controls retained |
| EVL-local | 6/18 | Actual Epsilon execution with experiment-authored integrity and intra-artifact rules |
| EVL-cross | 18/18 | Actual Epsilon execution after equivalent cross-artifact rules were authored |

Req2Web beats EVL-local in all six cases by +2/3 criteria per case. The exact
paired two-sided sign-flip value is `p=0.03125`. This value is descriptive and
exploratory because the family was selected after inspecting the broad result
and all cases are project-authored. It must not be presented as a preregistered
confirmatory test.

The original broad E2 results remain visible beside the secondary table:

- C1 and EVL-local: 18/24 detected, 12/24 exact targets, 0/6 clean alarms;
- C2 and EVL-cross: 24/24 detected, 24/24 exact targets, 0/6 clean alarms.

## Defensible advantage claim

The result supports this bounded statement:

> Across the complete six-workflow trace-relation fault family, Req2Web's
> integrated checker passed 18/18 detection, exact-localization and clean-control
> obligations, whereas Epsilon with integrity and intra-artifact rules passed
> 6/18. Adding equivalent cross-artifact rules to Epsilon restored parity at
> 18/18.

The advantage is therefore **native integration of project trace semantics**,
not a claim that external rule engines are incapable of expressing the same
checks. EVL-cross must remain in the result table. The external rules were
written by the Req2Web experiment team, not supplied by Epsilon upstream, so
neither EVL condition should be called an out-of-the-box product benchmark.

## Relationship to the failed Agent follow-up

The later v3-v5 diagnostic-Agent development runs remain immutable negative
protocol evidence. They did not realize the trace-index treatment and do not
alter this static result. No scorer relaxation can turn their `0/2` versus
`0/2` development outcomes into an advantage.

For the paper, this integrated trace-family result is the stronger completed E2
evidence. A future Agent navigation experiment may remain optional, but it is
not required to report the bounded integrated-checking advantage.

## Prospective confirmation

A stronger confirmatory claim would require at least 12 new unseen workflows,
the same three criteria, all cases retained, and both EVL-local and EVL-cross
conditions frozen before results. The broad table and external equivalence
must remain disclosed.

## Local artifacts

- Frozen strategy:
  `fixtures/phase7_e2_integrated_trace_acceptance_v1.json`
- Read-only fail-closed scorer:
  `scripts/phase7_e2_integrated_trace_acceptance.py`
- Focused tests:
  `tests/test_phase7_e2_integrated_trace_acceptance.py`
- Generated machine-readable analysis:
  `outputs/phase7_e2_integrated_trace_acceptance_v1/historical_secondary_analysis.json`
  (SHA-256
  `f158ceadb3381e01fccbb8bc6dcb04c2f055ea7d28193f3c40af2ec602c7fef5`)
- Generated readable summary:
  `outputs/phase7_e2_integrated_trace_acceptance_v1/historical_secondary_analysis.md`
  (SHA-256
  `3de59b8eda228e9e67821425eb54d5c275a0965ae2a84dbf8a8ca8c43e713884`)

The scorer binds the exact existing local and external observation bytes. It
does not run Req2Web, Epsilon, a model, a browser or any remote resource.
