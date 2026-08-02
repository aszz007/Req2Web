# Req2Web Phase 4 P4-00/P4-01 Agent-chain Contract Packet

**Status:** `accepted_local_no_model_contract`

**Base:** `d4f89ae051de5d15d0b4dade9a86b9fb70826ac1`

**Scope:** documentation-only, local, accepted no-model contract. This packet
defines the next implementation boundary; it does not implement a workflow,
install a dependency, invoke a model, create training data, train an adapter,
or authorize local/remote GPU work.

## 1. Current naming and historical aliases

Phase 4 is the current management name for the Agent-chain split and
deployment workstream. The historical Stage 3 M3-AC documents and commits are
source material and remain traceable under their original names. They are not
rewritten to claim that the historical work used the current Phase 4 name.

| Current authoritative work item | Historical source alias | Meaning and current boundary |
| --- | --- | --- |
| `P4-00` | `M3-AC-00` | Completed and accepted documentation, decision, memory, handoff, and naming synchronization. |
| `P4-01` | `M3-AC-01` | Accepted local no-model contract packet; no later slice or action is authorized. |
| `P4-02` | `M3-AC-02` | Future local synthetic LangGraph orchestration; separately authorized only after P4-01. |
| `P4-03` | `M3-AC-03` | Future below-10B local Qwen node pilot; requires model and action-time approval. |
| `P4-04` | `M3-AC-04` | Future node-specific LoRA go/no-go; requires node evidence and a separate data/training decision. |
| `P4-05` | `M3-AC-05` | Future controlled integrated AutoDL pilot; requires separate remote/GPU/cost/action-time approval. |
| `Phase 5` | Old planned `M4` work | Formal holdout work, which may later carry an approved `H1` evaluation. |
| `Phase 6` | Old planned `M5` work | Release and publication-rehearsal work. |

`H1`, `H1/gold`, and `Path 3 non-H1` are semantic evaluation and data-
isolation labels, not phase names. They keep their existing meanings and are
not renamed to `Phase 5`. Historical `M3`, `M3-AC`, `M4`, and `M5` labels in
dated evidence, commits, and reports remain unchanged; new work uses only the
current `P4-00` through `P4-05` identifiers and this table is the unique
supersession/alias mapping.

## 2. P4-00/P4-01 scope and non-goals

P4-00 has completed and accepted the current naming and the exact relationship
between historical M3-AC decisions and Phase 4. P4-01 freezes an accepted,
sufficiently implementable node-local contract for a later runtime; it is not
a runtime implementation or action authorization.

The P4-01 contract preserves the following existing authorities and outcomes:

* Deterministic B remains the only canonical requirement and use-case
  authority. B-Aux is an independent advisory sidecar and never writes back
  to B.
* F1 static structure, F2 state/visibility, F3 interaction graph, and F4
  candidate acceptance semantics are separate node-local outputs. The shared
  deterministic stable-ID registry runs only after each of F1, F2, F3, and F4
  and assigns or validates IDs only for that F node's new entities.
* After the F3 registry, deterministic Python authority creates the
  authoritative per-use-case mappings. F4 then consumes canonical B use-case
  IDs, the registered F1-F3 IDs, and the deterministic mapping artifact.
  Only after the F4 registry does deterministic composition create the strict
  `ModelSemanticCandidate` candidate for the existing parser/assembler/gates.
* F4 candidate acceptance semantics is not `RequirementView`,
  `AcceptancePlan`, binding, scripted/browser evidence, or a final
  acceptance verdict.
* LangGraph is the sole future workflow orchestration substrate. Req2Web/Python
  remains the sole authority for domain contracts, stable IDs, mappings,
  composition, gates, repair, fallback, and package routing. Agent
  Server/Studio is limited to API/checkpoint/stream/debug concerns; Coze is a
  possible later external UI and may not duplicate the workflow.
* The final `ModelSemanticCandidate` remains the single C2D end-to-end
  canonical composition target. F1-F4 node-local outputs are not silently
  converted into training targets.
* The P4-01 composition profile fixes final candidate `constraints=[]` and
  `claimed_attribution_edges=[]`. Canonical B/AgentContext constraints remain
  identity-bound validation inputs and are added to PageSpec only by the
  existing assembler as local constraints. Generic F refs remain audit/D17/
  replay metadata and never become candidate claimed-attribution edges.
  This profile does not modify or narrow the global `ModelSemanticCandidate`
  v1 or assembler contract.
* Historical strict Qwen model success remains `0/2`. Repair, closure,
  deterministic composition, and same-case G0 fallback are not model success.
* C2D remains `actual_pair_count=0` and `sealed_case_count=0`; human authoring
  remains paused until a separately approved target-contract decision.

P4-01 does not install LangGraph, write runtime code, call a model, select a
model or decoding profile, create or edit pairs, train, access H1/gold,
connect to a service, use a remote resource, operate GPU/AutoDL, or perform
formal-quality or browser evaluation.

## 3. Fixed no-action declaration

The following exact declaration is required at the packet level and in every
P4-01 record class. It means **verifiable but not authorizing**: a validator
may check that the flags are false, but a false flag is not an authority to
turn any action on.

```text
model_action=false
runtime_execution=false
dependency_installation=false
training=false
remote=false
```

The declaration is an exact-key object. Missing keys, extra keys, non-boolean
values, and `true` values fail closed. It cannot be replaced by a caller-
supplied receipt or by a checkpoint. A future action request must use a new
approved gate with its own action-time authority; it cannot mutate a P4-01
record.

The P4-01 record registry contains only `contract_packet`,
`node_input_projection`, `b_aux_sidecar`, `node_result`, `stable_id_registry`,
`use_case_mapping`, `composition`, `node_event`, `checkpoint`,
`d17_disposition`, and `failure_route`. `RawCapture` is the exact nested
artifact in the common envelope, not a separate record class. Each class has
one and only one `no_action` field with exactly the five keys above. No class
may add an action flag, omit a flag, or treat a receipt of one class as
authority for another.

### 3.1 Record registry and exact payload routing

The record registry revision is exact `p4.record.registry.v1`. Payloads for
`b_aux_sidecar`, `node_result`, `stable_id_registry`, `use_case_mapping`,
`composition`, `node_event`, `checkpoint`, and `d17_disposition` are defined
respectively in sections 6, 7.1, 8, 9, 9, 10.2, 11, and 12. The remaining
payloads are exact as follows.

`contract_packet` payload has exactly `packet_schema_version`,
`packet_status`, `base_commit_sha`, `alias_table_identity`,
`contract_identity`, and `record_registry_revision`:

* `packet_schema_version` is exact `p4.agent_chain.contract_packet.v1`.
* `packet_status` is exact `accepted_local_no_model_contract`.
* `base_commit_sha` is exact
  `d4f89ae051de5d15d0b4dade9a86b9fb70826ac1`.
* `alias_table_identity` and `contract_identity` are exact identity objects
  from section 5.2 and must be recomputed from their canonical bytes.
* `record_registry_revision` is exact `p4.record.registry.v1`.

`node_input_projection` payload has exactly `consumer_node_id`,
`canonical_b_identity`, `upstream_registry_identities`, `mapping_identity`,
`advisory_disposition_ref`, `d17_disposition_ref`, `logical_input_classes`,
`prohibited_input_classes`, `projection_identity`, `projection_status`, and
`failure`:

* `consumer_node_id` is one of `B-Aux`, `F1`, `F2`, `F3`, or `F4`.
* `canonical_b_identity` is a live-validated identity object;
  `upstream_registry_identities` is the topology-ordered array of preceding
  cumulative registry identities: empty for B-Aux and F1, `[F1]` for F2,
  `[F1,F2]` for F3, and `[F1,F2,F3]` for F4.
* `mapping_identity` is `null` for B-Aux and F1-F3 and is the validated
  deterministic mapping identity for F4.
* `d17_disposition_ref` is a required exact same-case/request/consumer Ref
  with `ref_type=d17_disposition` for every consumer, including the P4-01
  no-action B-Aux disposition. `advisory_disposition_ref` is `null` for
  B-Aux and is a required exact same-case/request/consumer Ref with
  `ref_type=advisory_disposition` for each F1-F4 consumer.
* `logical_input_classes` is an exact ordered, duplicate-free string array.
  P4-01 fixes the consumer arrays as follows:

  ```text
  B-Aux = [canonical_b_requirement_view,
           canonical_b_use_case_view,
           canonical_b_constraint_view,
           target_device,
           task_type,
           approved_structural_signals]

  F1 = [canonical_b_requirement_view,
        canonical_b_use_case_view,
        canonical_b_constraint_view,
        target_device,
        task_type,
        approved_structural_signals]

  F2 = [canonical_b_requirement_view,
        canonical_b_use_case_view,
        canonical_b_constraint_view,
        target_device,
        task_type,
        approved_structural_signals,
        f1_registered_structure_view]

  F3 = [canonical_b_requirement_view,
        canonical_b_use_case_view,
        canonical_b_constraint_view,
        target_device,
        task_type,
        approved_structural_signals,
        f1_registered_structure_view,
        f2_registered_state_visibility_view]

  F4 = [canonical_b_requirement_view,
        canonical_b_use_case_view,
        canonical_b_constraint_view,
        target_device,
        task_type,
        approved_structural_signals,
        f1_registered_structure_view,
        f2_registered_state_visibility_view,
        f3_registered_interaction_view,
        deterministic_use_case_mapping_view]
  ```

  P4-01 never includes `b_aux_advisory_view` because `disposition=used` is
  invalid. For an F1-F4 consumer, a future revised per-node D17 contract may
  append `b_aux_advisory_view` as the final class only when that exact consumer
  has a validated `used` disposition and a matching sidecar/D17 identity.
* `prohibited_input_classes` is the following exact ordered, duplicate-free
  array for every F1-F4 consumer:

  ```text
  [full_agent_context,
   retrieval_guidance,
   retrieval_evidence_content,
   retrieval_evidence_location,
   rico_or_reference_assets,
   h1_or_gold,
   evaluator_only_material,
   secrets_or_credentials,
   result_package,
   downstream_acceptance_evidence,
   g0_payload,
   unapproved_b_aux]
  ```

  For B-Aux it is the following exact ordered, duplicate-free array; the five
  final entries make the prohibition on every F output and deterministic
  mapping explicit:

  ```text
  [full_agent_context,
   retrieval_guidance,
   retrieval_evidence_content,
   retrieval_evidence_location,
   rico_or_reference_assets,
   h1_or_gold,
   evaluator_only_material,
   secrets_or_credentials,
   result_package,
   downstream_acceptance_evidence,
   g0_payload,
   unapproved_b_aux,
   f1_registered_structure_view,
   f2_registered_state_visibility_view,
   f3_registered_interaction_view,
   f4_registered_acceptance_view,
   deterministic_use_case_mapping_view]
  ```

  These names are logical contract categories, not actual Provider-visible
  bytes. A future node D17 contract must separately freeze exact fields and a
  serializer and cannot inherit the historical one-call D17 authority.
* `projection_identity` is a `canonical_json` identity when
  `projection_status=validated_no_action`, and `null` when
  `projection_status=blocked`.
* `failure` is `null` only for `validated_no_action`; `blocked` requires the
  exact `Failure` object from section 10.1.

The validator cross-checks logical classes against upstream identities and
consumer routing. B-Aux requires `upstream_registry_identities=[]`,
`mapping_identity=null`, `advisory_disposition_ref=null`, the exact six logical
classes above, and its exact expanded prohibited list. For F1-F4,
`f1_registered_structure_view`, `f2_registered_state_visibility_view`, and
`f3_registered_interaction_view` require the matching cumulative registry
identity, while `deterministic_use_case_mapping_view` requires the F4
`mapping_identity`; each F also requires its own advisory disposition Ref.
Every consumer requires its own same-scope D17 disposition Ref and matching
`canonical_b_identity`. A missing, extra, duplicated, or reordered class, an
identity/class/ref mismatch, or any selected prohibited class fails closed.
Class/identity/prohibition mismatch uses
`failure_code=input_projection_invalid`; canonical order drift uses
`failure_code=array_order_drift`. This projection is only the no-action
logical inventory; it carries no inline Provider payload.

`failure_route` payload has exactly `failed_record_ref`, `failure`,
`raw_capture_ref`, `route_status`, `downstream_blocked`, `retry_performed`,
`fallback_execution`, and `g0_decision_owner`:

* `failed_record_ref` is an exact same-case/request `Ref`; `failure` is the
  exact owning `Failure`; `raw_capture_ref` is an exact `raw_capture` Ref or
  `null` when no capture was created.
* `route_status` is exact `failed_closed_no_action`,
  `downstream_blocked=true`, and `retry_performed=false`.
* `fallback_execution` is exact `not_executed_p4_01` and
  `g0_decision_owner` is exact `future_existing_delivery_contract`. P4-01
  neither chooses nor executes G0 fallback.

Any new record type, payload key, status, or enum value requires a schema and
record-registry revision; an unknown value fails closed.

## 4. Authority hierarchy

The following order is normative for the future consumer of this packet:

1. Owner-approved decisions and the current P4 alias/supersession table.
2. The existing Req2Web/Python domain contracts and their owning validators,
   including the strict semantic-candidate parser, canonical assembler,
   RequirementView, AcceptancePlan, binding, one-repair, G0, and package gates.
3. Deterministic canonical B and its validated requirement/use-case identities.
4. Deterministic stable-ID registry, use-case mapping, and composition code.
5. A future LangGraph graph, which may schedule, checkpoint, interrupt, resume,
   and stream records but may not redefine items 1--4.
6. A generated node result, B-Aux sidecar, receipt, Agent Server/Studio view,
   or Coze UI, none of which can confer domain or action authority.

The consuming validator must recompute the authority-relevant identities at
the consumption point. A receipt is replay evidence, not self-certifying
authority. A constructor, `from_dict`, `from_bytes`, canonical serialization,
or checkpoint restore is not by itself a validation decision.

## 5. Common immutable envelope and identity contract

The envelope applies to every future node, registry, mapping, composition,
event, checkpoint, D17-disposition, and failure-route record. The P4-01
packet itself additionally carries the fixed no-action declaration above.

### 5.1 Envelope exact keys

Each record has exactly these top-level keys, in the schema sense (canonical
JSON ordering is defined below):

```text
record_type
record_version
phase_id
work_item
case_id
request_id
node_id
node_revision
source_kind
status
no_action
input_refs
output_refs
raw_capture
authority_refs
replay
payload
```

Required types and allowed values:

| Key | Required type and constraint |
| --- | --- |
| `record_type` | Non-empty ASCII string from the P4 record registry; unknown types fail closed. |
| `record_version` | Exact string `p4.record.v1`. |
| `phase_id` | Exact string `phase4`. |
| `work_item` | Exact string `P4-01` for this packet; `P4-00` is allowed only for the naming record. |
| `case_id` | Non-empty canonical case identifier, or the exact sentinel `not_applicable_p4_contract` for a packet-level record. |
| `request_id` | Non-empty request identity bound to `case_id`, or the exact sentinel `not_applicable_p4_contract` for a packet-level record. |
| `node_id` | One of `B`, `B-Aux`, `F1`, `F2`, `F3`, `F4`, `registry`, `mapping`, `composition`, or `workflow`. |
| `node_revision` | Non-empty version string; it is part of every node identity and cannot be inferred from a display label. |
| `source_kind` | One of `contract_candidate`, `synthetic_fixture`, `local_replay`, `model_candidate`, `deterministic_projection`, or `deterministic_composition`. A future real-provider kind requires a separately approved authority and is not available in P4-01. |
| `status` | One of `not_run`, `captured`, `validated`, `failed_closed`, `interrupted`, `resumed`, or `cancelled`. `resumed` is valid only for a checkpoint or node event; section 7.1 defines the narrower `node_result` subset. |
| `no_action` | The exact five-key boolean object in section 3, with all values `false`. |
| `input_refs` | Array of exact `Ref` objects; no inline payload or unbound path is accepted. |
| `output_refs` | Array of exact `Ref` objects; a `node_result` may use an empty array for `not_run`, `failed_closed`, `interrupted`, or `cancelled` under section 7.1. |
| `raw_capture` | Exact `RawCapture` object in section 5.3. |
| `authority_refs` | Array of exact authority references; a record with a missing required authority fails closed. |
| `replay` | Exact `ReplayBinding` object in section 5.4. |
| `payload` | Exact object for `record_type`; extra or missing payload keys fail closed. |

`record_identity` is not caller input. A consumer derives it from the
canonical record body without a self-referential identity field and compares
it with any enclosing inventory or event reference. Every nested identity is
recomputed rather than trusted from a supplied digest.

### 5.2 References and identities

Every `Ref` object has exactly `ref_type`, `ref_id`, and `ref_revision`.
`ref_type` is a closed enum covering `b_requirement`, `b_use_case`,
`b_constraint`,
`b_aux_sidecar`, `f1_local`, `f2_local`, `f3_local`, `f4_local`,
`registry_stable`, `contract_packet`, `node_input_projection`, `node_result`,
`stable_id_registry`, `mapping`, `composition`, `raw_capture`, `node_event`,
`checkpoint`, `advisory_disposition`, `d17_disposition`, `failure`,
`failure_route`, `d17_manifest`, `d17_policy`, `d17_input_view`, and
`g0_reference`. `ref_id` is a canonical identifier; `ref_revision` is a
non-empty string. A reference never carries the referenced payload. Any
additional `ref_type` requires a record schema revision.

Every identity object has exactly `identity_kind`, `sha256`, `byte_length`,
and `revision`:

* `identity_kind` is one of `canonical_json`, `raw_bytes`,
  `canonical_row_list`, `canonical_tree`, or `archive`.
* `sha256` is lowercase `sha256:` followed by exactly 64 hexadecimal digits.
* `byte_length` is a non-negative integer and a JSON boolean is not an integer.
* `revision` is a non-empty contract/revision string.

`raw_bytes`, `canonical_json`, `canonical_row_list`, `canonical_tree`, and
`archive` identities are distinct domains. A row-list hash cannot stand in for
a tree or archive hash, and a parsed output hash cannot stand in for raw
bytes.

### 5.3 Raw-first capture

`RawCapture` has exactly `state`, `encoding`, `byte_length`, `sha256`, and
`bytes_b64`.

* `state` is `not_created`, `captured`, `absent`, or `failed`.
* `encoding` is exact `base64_standard` when `state=captured`, and
  `not_applicable` otherwise.
* `byte_length` is the exact raw byte count; a captured value must be
  positive, and every other state requires exact `0`.
* `sha256` is the raw-byte identity, or the exact sentinel
  `sha256:0000000000000000000000000000000000000000000000000000000000000000`
  only for `not_created`/`absent`/`failed`.
* `bytes_b64` is canonical standard base64 for `captured`, and `null` for
  every other state. Noncanonical padding, alternate alphabets, or a decoded
  length/hash mismatch fail closed.

Raw capture is committed before parsing, registry work, repair, composition,
or routing. A parse tree, repaired candidate, receipt, or fallback package
cannot be presented as the raw model/provider output. P4-01 creates no actual
model raw capture; this is the future record contract only.

### 5.4 Canonicalization and replay

`ReplayBinding` has exactly `canonicalization`, `record_sha256`,
`record_byte_length`, `case_sha256`, `request_sha256`, `contract_sha256`,
and `replay_status`.

`canonicalization` is exact `canonical_json_v1`: UTF-8 without BOM, Unicode
NFC strings, no insignificant whitespace, lexicographically sorted object
keys, schema-prescribed array order, JSON `true`/`false`/`null`, no NaN or
infinity, and no floating-point values unless a later schema explicitly
allows them. Integer fields reject boolean values. Unknown keys, duplicate
keys, invalid Unicode, noncanonical base64, or alternate numeric spellings
fail closed.

`replay_status` is one of `unverified`, `verified`, `drifted`, or
`failed_closed`. A verified replay must reparse the exact bytes, recompute
every nested identity, check the case/request/contract bindings, and confirm
the same source-kind and node revision. Resume, routing, and composition are
blocked for any status other than `verified`.

All public create/from-dict/from-bytes/to-dict/canonical-bytes/hash/validate
boundaries must perform live validation or a fresh canonical reparse. Direct
object construction followed by mutation is not an authority path.

### 5.5 Complete array-order rules

Canonical order is semantic contract state, not presentation. Validators must
apply all of the following rules:

* Generic Ref arrays (`input_refs`, `output_refs`, `authority_refs`,
  `local_refs`, every field named `refs`, and other audit-only Ref arrays) are
  sorted by the exact `(ref_type, ref_id, ref_revision)` tuple and contain no
  duplicates. Schema-specific semantic reference arrays such as
  `use_case_refs` follow their owning order rule instead.
* B-Aux `advisory_items` preserves validated raw source order exactly; items
  are never sorted by kind, statement, target, hash, or display value. Within
  each item, `target_b_refs` follows the generic Ref tuple order and is
  duplicate-free. Duplicate exact advisory items are rejected.
* `node_input_projection.logical_input_classes` follows the exact B-Aux or
  F1-F4 consumer list in section 3.1, with any future eligible
  `b_aux_advisory_view` for an F consumer only in its specified final position.
  `prohibited_input_classes` follows the exact consumer-specific list in
  section 3.1: the common list for F1-F4 or the expanded list for B-Aux.
  Set-equivalent reordering is invalid.
* F1 `sections` array order is the sole layout section order. Registry section
  assignments and final `layout.section_stable_ids` preserve that order
  exactly; dictionary, hash, title, or stable-ID sorting is forbidden.
  F1 `components` is ordered by `sections`, then by first appearance in each
  section's `component_local_ids`. Every component appears exactly once.
* F2 `states`, F3 `interactions`, and F4 `acceptance_checks` preserve their
  node-validated semantic order. Registry `assignments` preserves the exact
  `entity_rows` order. Each F2 `visible_component_local_ids` array is the
  F1 canonical component order filtered to the visible set.
* `ordered_mappings` follows canonical B use-case order. Within each row,
  `section_stable_ids` and `component_stable_ids` follow the corresponding F1
  canonical order, while `interaction_stable_ids` follows F3 canonical order.
* Node events are ordered only by strictly increasing `event_seq`.
  Checkpoint `completed_node_ids` and `pending_node_ids` are ordered by the
  frozen graph topology, never by completion timestamp or lexical sorting.

If a prior canonical order exists, any reordered, multiply represented, or
partially reordered array fails closed even when it contains the same set of
values.

## 6. B, B-Aux, and per-F advisory disposition

Deterministic B runs first and remains canonical. Its requirement, normalized
use-case, and constraint identities are the only authoritative inputs for
mapping and composition validation. Under the P4-01 composition profile,
constraint values are not copied into the candidate; the unchanged assembler
adds local canonical constraints to PageSpec. B-Aux may be requested after B
and may produce an independent advisory sidecar, but it cannot mutate,
replace, or annotate the canonical B object in place.

### 6.1 Exact B-Aux sidecar schema

`b_aux_sidecar` is the only B-Aux sidecar record type. It uses the common
envelope with `record_type=b_aux_sidecar` and `node_id=B-Aux`. Its payload has
exactly:

```text
advisory_revision
call_count
canonical_b_identity
advisory_items
sidecar_status
failure
```

`advisory_revision` is a non-empty canonical revision string.
`call_count` is a non-boolean integer in `0..1` and records the actual B-Aux
generation-call count for both `advisory_available` and
`advisory_unavailable`. P4-01 fixes it to `0`. A future B-Aux execution may set
it to `1` only after B-Aux's own D17 action-time approval; it can never exceed
one and cannot be incremented by an automatic retry.
`canonical_b_identity` is a live-recomputed identity for the same case and
request as the envelope. Each `advisory_items` entry has exactly:

```text
advisory_kind
statement
target_b_refs
```

`advisory_kind` is exactly one of `ambiguity`, `conflict`,
`missing_information`, `risk`, or `suggestion`. `statement` is non-empty
canonical text. `target_b_refs` is a non-empty generic Ref array containing
only `b_requirement`, `b_use_case`, or `b_constraint` refs for the same case
and canonical B identity; it is tuple-sorted and duplicate-free under section
5.5. `advisory_items` itself preserves validated raw source order exactly.
Duplicate exact items, including duplicate canonical item bytes, fail closed.

Advisory items have no local, stable, or final entity IDs. The stable-ID
registry never receives or processes them. `sidecar_status` is exactly
`advisory_available` or `advisory_unavailable`:

* `advisory_available` requires a non-empty `advisory_items`, `failure=null`,
  and envelope `status=validated`.
* `advisory_unavailable` requires `advisory_items=[]`, an exact Failure with
  `failure_code=advisory_unavailable` and
  `failure_stage=b_aux_sidecar`, and envelope `status=failed_closed`.

The sidecar validator binds envelope case/request, live canonical B identity,
raw-first capture, source kind, replay identity, and payload identity before a
per-F disposition may reference it. A parsed/advisory object cannot replace
the captured raw bytes, and the sidecar cannot write back to canonical B.
P4-01 creates no B-Aux sidecar and fixes the B-Aux call count to `0` as a
profile invariant. Any future B-Aux call requires its own node-specific D17
action-time approval, is limited to the one call recorded in the exact
`call_count` payload field, and is never automatically retried. B-Aux failure
records `advisory_unavailable` but does not block or modify canonical B.

A B-Aux `node_input_projection` contains only the following exact ordered
logical classes:

```text
[canonical_b_requirement_view,
 canonical_b_use_case_view,
 canonical_b_constraint_view,
 target_device,
 task_type,
 approved_structural_signals]
```

In P4-01 these are no-action logical categories and carry no Provider-visible
payload. Future Provider-visible fields for `target_device`, `task_type`, and
`approved_structural_signals` are eligible only when B-Aux's own D17 contract
approves their exact fields and serializer. B-Aux may not consume any F
output, including `f1_registered_structure_view`,
`f2_registered_state_visibility_view`, `f3_registered_interaction_view`,
`f4_registered_acceptance_view`, or `deterministic_use_case_mapping_view`, and
it prohibits every class in the common F prohibited classes plus the B-Aux-
specific F-output prohibitions in section 3.1.

### 6.2 Per-F advisory disposition

The per-F disposition is an exact object with exactly:

```text
advisory_node_id
consumer_node_id
disposition
sidecar_ref
sidecar_sha256
reason_code
d17_ref
```

`advisory_node_id` is exact `B-Aux`; `consumer_node_id` is exactly one of
`F1`, `F2`, `F3`, or `F4`. There is one independently validated disposition
record per consumer node; a disposition for one F node cannot be reused by
another. `disposition` is one of `absent`, `failed`, `used`, or `ignored`:

* `absent`: no sidecar exists; F must use only its approved canonical inputs.
* `failed`: an eligible sidecar attempt failed; the failure is retained and
  cannot be silently retried or converted to canonical B data.
* `used`: the sidecar was explicitly included in the per-node D17 input view
  and independently validated for identity and scope.
* `ignored`: a sidecar exists but is not eligible or was intentionally not
  consumed; it must not influence F.

`sidecar_ref` and `sidecar_sha256` are `null` only for `absent`; they are
required for `failed`, `used`, and `ignored`, with `sidecar_ref.ref_type` exact
`b_aux_sidecar`. `sidecar_sha256` is a string in the section 5.2 SHA-256
format: lowercase `sha256:` followed by exactly 64 hexadecimal digits. The
consumer validator live-recomputes the exact canonical bytes of the referenced
section 6.1 sidecar record and requires `sidecar_sha256` to match that digest
and the same case/request/canonical B identity; a caller-supplied digest is not
authority.
`failed` requires that record's `sidecar_status=advisory_unavailable`;
`used` and `ignored` require `sidecar_status=advisory_available`.
`reason_code` is fixed by `disposition`:
`absent -> sidecar_absent`, `failed -> advisory_unavailable`,
`used -> advisory_used_under_d17`, and `ignored -> advisory_ignored`.
`d17_ref` is `null` for every P4-01 disposition because `used` is invalid in
this no-action profile. In a future revised action contract, `used` requires
an exact `Ref` with `ref_type=d17_disposition`, bound to the same case,
request, and `consumer_node_id`; an untyped string sentinel is never a Ref.

Without a separately approved per-node D17 input view, `used` is invalid and
the sidecar is not allowed to enter any F input. B-Aux absence or failure is
nonblocking only when the approved future node contract explicitly allows
that disposition; it never changes B. Generic refs carried by B-Aux or any F
record are audit/D17/replay metadata only and cannot become final candidate
semantics or claimed-attribution edges.

## 7. F node-local schemas

Each F node has one `NodeResult` envelope and one exact node-local `payload`.
The node may propose local IDs only. It cannot assign a final shared stable ID
or create a use-case mapping. Every node result must bind its input refs,
raw-first event, node revision, advisory disposition, and replay identity.

### 7.1 Shared node-result rules

The `NodeResult` payload has exactly `node_id`, `call_count`,
`local_output_identity`, `local_refs`, `failure`, and `node_output`.

* `node_id` is the envelope node ID and is one of `F1` through `F4`.
* `call_count` is an integer in `0..1`; P4-01 fixes it to `0` and future
  generation cannot exceed one call.
* A `node_result` status is exactly one of `not_run`, `validated`,
  `failed_closed`, `interrupted`, or `cancelled`; it never uses `captured` or
  `resumed`. Resume is represented only by node events and checkpoints.
* `local_output_identity` is a `canonical_json` identity only for
  `status=validated`; it is `null` for every other status.
* `local_refs` is an array of exact refs to the node's local IDs for
  `validated` and is empty for every other status.
* `failure` is `null` for `validated` and `not_run`; `failed_closed`,
  `interrupted`, and `cancelled` require the exact `Failure` object in
  section 10.1.
* `node_output` is the exact payload schema below only for `validated`; it is
  the exact empty object for `not_run`, `failed_closed`, `interrupted`, and
  `cancelled`. Envelope `output_refs` follows the same rule.

Every generated entity includes the common fields `local_id`, `entity_type`,
and `refs` in addition to only the node-specific keys listed below; there is
no standalone `LocalRef` record. `local_id` is a non-empty ASCII string unique
within the node output. `entity_type` is exact `section` or `component` for
F1, `state` for F2, `interaction` for F3, and
`candidate_acceptance_check` for F4. `refs` is a generic Ref array ordered by
section 5.5; it is audit/D17/replay metadata only and contains no free-form
payload or final candidate semantics.

An `interrupted` or `cancelled` result may retain an already committed
`RawCapture(state=captured)` in its envelope, but it cannot produce a
validated node output, registry success, mapping/composition success, or an
automatic retry. Raw/input/output identities, D17 records, registry records,
failures, events, and replay bindings remain envelope metadata; they are not
node-local semantic sidecars or final candidate fields.

### 7.2 F1 static structure payload

F1 `node_output` has exactly `page_title`, `layout_pattern`, `sections`, and
`components`. These fields are F1-owned semantic content. `page_title` and
`layout_pattern` are non-empty canonical text. The `sections` array order is
the sole layout order; there is no separate `section_order` field.

Each section has exactly:

```text
local_id
entity_type
title
purpose
component_local_ids
refs
```

`entity_type` is exact `section`; `title` and `purpose` are non-empty canonical
text; `component_local_ids` is an ordered, duplicate-free array of F1
component local IDs; and `refs` is an audit-only generic Ref array.

Each component has exactly:

```text
local_id
entity_type
component_type
section_local_id
label
purpose
refs
```

`entity_type` is exact `component`; `component_type` is non-empty canonical
text accepted by the existing owning `SemanticComponent` contract and
validator, not a P4-01 closed enum. `section_local_id` must identify the one
owning F1 section; `label` and `purpose` are non-empty canonical text; and
`refs` is an audit-only generic Ref array. The `components` array follows the
section order and, within each section, the first-appearance order of that
section's `component_local_ids`. Every component is listed exactly once, and
its `section_local_id` must match that occurrence.

The exact deterministic projection is: `page_title` to the final candidate
title; `layout_pattern` plus the exact `sections` order to final layout;
section `title`/`purpose` and registry-stable IDs to final sections; and
component `component_type`/`label`/`purpose`, owning registered section ID,
and registry-stable ID to final components. Section and component
`use_case_ids` are not F1 fields and are supplied only by deterministic
mapping. F1 refs are never projected as claimed-attribution edges.

F1 fails closed for duplicate local IDs, missing section/component refs,
component coverage/order drift, a component assigned to more than one
section, an empty or owning-validator-invalid component type, or an unbound B
identity. Composition cannot invent hierarchy, order, or component semantics.

### 7.3 F2 state/visibility payload

F2 `node_output` has exactly `states`.

Each state has exactly:

```text
local_id
entity_type
name
description
visible_component_local_ids
refs
```

`entity_type` is exact `state`; all component IDs refer to F1 local IDs;
`name` and `description` are non-empty strings; `visible_component_local_ids`
is the F1 canonical component order filtered to the visible set and contains
no duplicate; and `refs` is an audit-only generic Ref array. The deterministic
projection is `name` to `SemanticState.name`, `description` to
`SemanticState.description`, and registered visible component IDs to
`SemanticState.visible_component_stable_ids`.

F2 never emits visibility-rule sidecars, component-state reverse indexes,
interaction transitions, or browser observations. It fails closed on an empty
or missing `name`/`description`, dangling or duplicate F1 component refs,
duplicate state IDs, visible-component order drift, or an unbound B/F1 input
identity. F2 refs are never projected as claimed-attribution edges.

### 7.4 F3 interaction-graph payload

F3 `node_output` has exactly `interactions`.

Each interaction has exactly:

```text
local_id
entity_type
trigger_component_local_id
source_state_local_id
action
target_state_local_id
user_feedback
refs
```

`entity_type` is exact `interaction`; `trigger_component_local_id` resolves to
an F1 component; `source_state_local_id` and `target_state_local_id` resolve
to F2 states; `action` and `user_feedback` are required non-empty strings.
`refs` is an audit-only generic Ref array. The interactions array preserves
the F3-validated semantic order.

F3 emits no `use_case_ids`: deterministic mapping injects those canonical B
use-case IDs after the F3 registry. A same-state transition is valid for
semantics such as filter or refresh; equal source and target states are not a
failure by themselves.

F3 fails closed on dangling or duplicate F1/F2 refs, duplicate interaction
IDs, missing required semantic fields, impossible state refs, or interaction
order drift. The exact deterministic projection is
`trigger_component_local_id` to the canonical trigger component,
`source_state_local_id` to source state, `action` to action,
`target_state_local_id` to target state, and `user_feedback` to user feedback;
the ordered mapping then supplies canonical `use_case_ids`. F3 refs are never
projected as claimed-attribution edges.

### 7.5 F4 candidate acceptance-semantics payload

F4 `node_output` has exactly `acceptance_checks`. F4 input must bind the
deterministic mapping identity and its ordered mappings; F4 may consume
mappings but may not recreate, edit, or infer them.

Each candidate acceptance check has exactly:

```text
local_id
entity_type
description
use_case_refs
state_ref
refs
```

`entity_type` is exact `candidate_acceptance_check`; `description` is the
non-empty exact expected-observation string and is copied byte-for-byte into
the final `SemanticAcceptanceCheck.description`; `use_case_refs` is a
non-empty array of canonical B use-case refs in canonical B use-case order;
`state_ref` is one registered F2 stable-state ref; and `refs` is an audit-only
generic Ref array. If an implementation uses an internal name
`expected_observation`, it must be identical to `description` and cannot be
transformed or guessed by composition. F4 does not emit support-claim
sidecars, `passed`, `failed`, browser evidence, an
`AcceptancePlan`, a binding, a final verdict, or a G1/G2 status. It only
describes candidate semantics for the deterministic downstream compiler.

F4 fails closed on an empty description, missing/unknown canonical B use-case
ref, a dangling or unregistered state ref, a mapping identity mismatch, an
authoritative acceptance field, a browser/evaluator claim, or a
`passed`/`accepted` value that attempts to cross the acceptance-layer
boundary. F4 still does not emit pass/fail/verdict, and its refs are never
projected as claimed-attribution edges.

## 8. Stable-ID registry after F1-F4

After each of F1, F2, F3, and F4, the shared deterministic registry receives
that F node's validated local output and assigns or validates stable IDs for
that F node's new entities. The registry never runs after B-Aux and never
assigns a final canonical ID to an advisory entity. B-Aux is handled only by
the sidecar identity/schema/hash/replay validator in section 6; its sidecar
identity may be recorded as an advisory ref but is not a `registry_stable`
ref and cannot enter canonical composition as an entity.

The registry is the only component allowed to assign or validate final stable
IDs for newly created F entities. It does not rename canonical B requirement
or use-case IDs.

The `stable_id_registry` payload has exactly `registry_input` and
`registry_output`. `registry_input` has exactly `node_id`, `node_output_identity`,
`entity_rows`, `registry_revision`, and
`prior_cumulative_registry_identity`; `node_id` is exactly one of `F1`,
`F2`, `F3`, or `F4`. Each `entity_row` has exactly `entity_type`, `local_id`,
`parent_refs`, `ordered_fields`, and `refs`. Allowed entity types are
`section`/`component` for F1, `state` for F2, `interaction` for F3, and
`candidate_acceptance_check` for F4. `parent_refs` contains only the
schema-required structural/semantic dependency refs (empty for F1 sections),
`ordered_fields` is the exact node-owned semantic field projection, and
`refs` remains audit-only. `entity_rows` follows section 5.5.

For F1, `prior_cumulative_registry_identity` is the exact identity of the
canonical UTF-8 bytes `[]`:

```text
identity_kind=canonical_row_list
sha256=sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945
byte_length=2
revision=p4.cumulative.registry.inventory.v1
```

For F2-F4 it must equal the immediately preceding registry output's live-
recomputed `cumulative_registry_identity`.

`registry_output` has exactly `assignments`,
`cumulative_inventory`, `unresolved_refs`, `registry_identity`,
`cumulative_registry_identity`, and `failure`. Each cumulative inventory row
has exactly `node_id`, `entity_type`, `local_id`, `stable_id`, and
`canonical_key_sha256`. The inventory contains all prior rows plus the current
rows, ordered by frozen graph topology and then current `entity_rows` order:
all F1 sections/components, F2 states, F3 interactions, and F4 acceptance
checks must be present by the F4 step.

Each assignment has exactly `entity_type`, `local_id`, `stable_id`,
`canonical_key_sha256`, `resolved_refs`, and `assignment_status`. The stable ID
is derived deterministically from the case/request/node revision, entity type,
canonical parent refs, ordered fields, and occurrence order. The full
canonical key identity is retained; a display label is never an ID authority.
`assignment_status` is `assigned` or `validated_existing`. `assignments`
preserves `entity_rows` order. `registry_identity` is a `canonical_json`
identity for the current registry result;
`cumulative_registry_identity` is the `canonical_row_list` identity of the
exact `cumulative_inventory` bytes. On validated success `unresolved_refs` is
empty and `failure=null`; otherwise the record is `failed_closed`, carries the
exact registry Failure, and cannot publish either identity as a successful
registry step.

The registry must reject duplicate local IDs, any stable-ID conflict across
types or nodes in the cumulative inventory, prior-cumulative identity drift,
hash or revision drift, cross-case/request references, ambiguous occurrence
order, unknown entity types, and unresolved refs. It must not silently rename
a canonical B requirement/use-case or let a node supply a final ID that
bypasses registry validation. The registry only registers and maps IDs; it
cannot reorder, add, remove, or rewrite node semantics and is never a second
semantic authority. Registry records are immutable and replayable.

## 9. Deterministic use-case mapping and composition

After F3 registry completion, deterministic Python authority creates the
authoritative per-use-case mappings. Mapping input is only the canonical B
use-case IDs, the identity-bound canonical B/AgentContext constraints, the
registered F1/F2/F3 IDs, and the validated input identities listed below.
Constraints participate in binding and validation only; they are not copied
into the final candidate. Mapping cannot depend on F4 output or F4 refs. A
B-Aux identity/disposition may be recorded as advisory input only when its
per-F disposition is `used` under a valid D17 binding; it never becomes a
mapping authority.

The mapping payload has exactly `mapping_revision`, `input_identities`,
`ordered_mappings`, `status`, and `failure`.

* `mapping_revision` is a non-empty revision string.
* `input_identities` has exactly `b_identity`, `constraint_identity`,
  `f1_cumulative_registry_identity`, `f2_cumulative_registry_identity`,
  `f3_cumulative_registry_identity`, `f1_advisory_identity`,
  `f2_advisory_identity`, and `f3_advisory_identity`. Each advisory identity
  is the validated sidecar identity only when that consumer's disposition is
  `used` under a future D17 contract, and is otherwise `null`; none grants
  stable-ID or mapping authority. All three are `null` in P4-01.
* `ordered_mappings` is ordered by the canonical B use-case order. Each row
  has exactly `use_case_id`, `section_stable_ids`, `component_stable_ids`,
  and `interaction_stable_ids`, matching the existing
  `UseCaseSemanticMapping` contract. The arrays are ordered, duplicate-free
  stable-ID lists; there is no `state_stable_ids` field and no global array
  that substitutes for per-use-case rows.
* `status` is `mapped`, `incomplete`, or `failed_closed`.
* `failure` is `null` only for `mapped`; otherwise it is the exact `Failure`
  object from section 10.

Every canonical B use case must have exactly one row. Missing, duplicate,
unknown, or unsupported mappings fail closed and cannot be filled by an
unbounded model response. `mapped` requires the complete ordered rows;
`incomplete` or `failed_closed` carries `ordered_mappings=[]`. The mapping
artifact is complete before F4 starts.
F4 consumes its identity and ordered rows, and the F4 registry then completes
before composition.

The composition payload has exactly `composition_profile`, `b_identity`,
`constraint_identity`, `registry_identities`, `mapping_identity`,
`f4_candidate_identity`,
`model_semantic_candidate_schema_version`,
`model_semantic_candidate_canonical_b64`,
`model_semantic_candidate_byte_length`, `model_semantic_candidate_sha256`,
`composition_status`, and `failure`. The status is `composed`, `incomplete`,
or `failed_closed`; `failure` is `null` only for `composed`.
`composition_profile` is exact `p4_01_empty_constraints_edges_v1`.
`b_identity`, `constraint_identity`, `mapping_identity`, and
`f4_candidate_identity` are live-recomputed identity objects.
`registry_identities` has exactly `f1_cumulative`, `f2_cumulative`,
`f3_cumulative`, `f4_cumulative`, and `final_cumulative`; each is a
`canonical_row_list` identity and `final_cumulative` must equal
`f4_cumulative`. `composition_status=incomplete` or `failed_closed` requires
the exact `Failure` object and requires
`model_semantic_candidate_schema_version=null`,
`model_semantic_candidate_canonical_b64=null`,
`model_semantic_candidate_byte_length=null`, and
`model_semantic_candidate_sha256=null`. For `composed`, schema version is a
non-empty owning-contract value, canonical base64 is non-empty, byte length is
a positive non-boolean integer, and SHA-256 has the section 5.2 format.

`model_semantic_candidate_canonical_b64` is canonical standard base64 of the
deterministically composed candidate's canonical bytes; it is not a raw model
output. `model_semantic_candidate_byte_length` and
`model_semantic_candidate_sha256` must match those exact bytes, and
`model_semantic_candidate_schema_version` must match the existing owning
`ModelSemanticCandidate` schema. Composition must live-reparse those bytes,
reconstruct through the existing `ModelSemanticCandidate.from_dict`/canonical
bytes path, and run the existing assembler. It retains the source/provenance
chain and cannot use a caller-supplied parsed object as authority.

### 9.1 Exact final `ModelSemanticCandidate` field-source matrix

The composition authority is the sole writer of the final candidate. Each
final field has one owning source or authority below; where a final row has a
subfield supplied by the mapping, that subfield owner is explicit and cannot
be overridden by the structural/interaction owner. There is no merge,
fallback, or composer inference between competing values. A missing,
ambiguous, or conflicting source value makes composition `incomplete` or
`failed_closed`.

| Final field | Sole owning source/authority | Exact deterministic projection and boundary |
| --- | --- | --- |
| `schema_version` | Existing `ModelSemanticCandidate` schema authority | Copy the approved schema version exactly; reject drift before reparse. |
| `title` | F1 `page_title` | Copy the non-empty F1 page title exactly; the composer cannot invent or rewrite it. |
| `layout` | F1 `layout_pattern` and `sections` array-order projection authority | Copy the F1 layout pattern; set `layout.section_stable_ids` to the registry section IDs in exactly the original F1 `sections` order. No lexical/hash/title reordering and no F2-F4 or B-Aux layout value are allowed. |
| `sections` | F1 registered section projection; `section.use_case_ids` subfield owned only by deterministic mapping | Project F1 section title, purpose, ordered component refs, and registered stable IDs in F1 section order; inject only the matching mapping row's ordered canonical use-case IDs. |
| `components` | F1 registered component projection | Project only `stable_id`, `section_stable_id`, `component_type`, `label`, and `purpose`; the composer cannot invent or normalize a component type. |
| `states` | F2 registered state/visibility projection | Project `name`, `description`, and registered `visible_component_stable_ids` in F2 semantic order. |
| `interactions` | F3 registered interaction projection; `interaction.use_case_ids` subfield owned only by deterministic mapping | Project trigger component, source state, action, target state, and user feedback; inject only the matching mapping row's ordered canonical use-case IDs. Same-state transitions remain valid. |
| `constraints` | P4-01 composition-profile constant | Emit exact `[]`. Canonical B/AgentContext constraints bind identity and are validated, but the composer does not copy them into the candidate; the existing assembler adds them locally to PageSpec under its unchanged authority. |
| `acceptance_checks` | F4 registered candidate acceptance-semantics projection | Project exact `description`, canonical B use-case refs, and registered F2 state refs; never project pass/fail, verdict, browser evidence, or G1/G2 status. |
| `use_case_mappings` | Deterministic authoritative mapping artifact | Copy the exact ordered per-use-case mappings produced after F3 registry and before F4; no F4 ref may alter them. |
| `claimed_attribution_edges` | P4-01 composition-profile constant | Emit exact `[]`. Generic F or B-Aux refs remain audit/D17/replay metadata; none may be projected or guessed as a candidate claimed edge. |

`section.use_case_ids` and `interaction.use_case_ids` have no source other
than the deterministic mapping rows. The matrix is also the complete field
ownership inventory: any omitted field, duplicate owner, source conflict,
unregistered ID, or unauthorized attribution edge is a composition failure,
not a prompt for repair or inference.

The empty `constraints` and `claimed_attribution_edges` values are specific
to `p4_01_empty_constraints_edges_v1`. They do not modify or narrow the global
`ModelSemanticCandidate` v1 parser or existing assembler. Any future non-empty
candidate constraints or claimed edges require a revised composition profile
with an explicit owning source, data/provenance policy, and per-node D17
contract; they cannot be enabled by changing a prompt or reinterpreting generic
refs.

Composition cannot alter the authoritative B requirement/use-case meaning,
invent missing refs, convert F4 candidate semantics into an acceptance
verdict, or bypass RequirementView, AcceptancePlan, binding, Acceptance,
G1/G2, one-repair, or G0 rules. A composed candidate is still only a model
candidate until the existing downstream gates say otherwise.

## 10. Failure, raw event, and source contracts

### 10.1 Failure object and fail-closed routing

Every failed, interrupted, or cancelled record has an exact `Failure` object
with `failure_code`, `failure_stage`, `retry_allowed`, `fallback_allowed`,
`source_refs`, and `message_code`. `not_run` may have no Failure. The complete
P4-01 `failure_code` list is:

```text
schema_invalid
canonicalization_invalid
identity_mismatch
raw_capture_invalid
source_kind_mismatch
authority_missing
no_action_violation
d17_not_approved
d17_binding_invalid
advisory_unavailable
advisory_disposition_invalid
input_projection_invalid
reference_unresolved
array_order_drift
registry_conflict
mapping_incomplete
composition_incomplete
parser_rejected
checkpoint_drift
node_interrupted
node_cancelled
downstream_blocked
```

`failure_stage` is exactly one of `envelope`, `input_projection`,
`raw_capture`, `b_aux_sidecar`, `node_validation`, `d17_gate`, `registry`, `mapping`,
`composition`, `parser_assembler`, `checkpoint`, or `failure_route`.
`retry_allowed=false` and `fallback_allowed=false` for every P4-01 Failure.
`source_refs` is an audit-only generic Ref array. `message_code` is the exact
ASCII string `p4_01_` followed by the selected `failure_code`; free-form
messages cannot change the route. Any new failure code/stage/message rule
requires a schema revision. Unknown codes, a missing required Failure, hash
drift, scope mismatch, parser failure, registry collision, missing D17, source
mixing, checkpoint drift, or no-action flag drift route fail closed.

The P4-01 failure route preserves any committed raw-first capture and ordered
node events, writes the immutable failure/failure-route record, and blocks all
downstream execution. An interrupted or cancelled result may retain captured
raw bytes but cannot become `validated`, model success, or `composed`, and it
cannot trigger an automatic retry. Only a future existing delivery contract
may decide whether its independently verified same-case frozen G0 is eligible;
P4-01 does not choose, materialize, copy, route, or execute fallback. It also
does not retry, repair, switch model/profile/quantization, offload to CPU,
relax prompts, or relax the parser.

### 10.2 Node events

The event payload has exactly `event_seq`, `event_kind`, `node_id`,
`case_id`, `request_id`, `input_identity`, `raw_capture_ref`, `output_ref`,
`status`, and `failure_ref`. `event_seq` is a strictly increasing integer
within one case/request and rejects boolean values. `node_id`, `case_id`, and
`request_id` must equal the owning envelope scope. `input_identity` is a live-
validated identity object. `raw_capture_ref`, `output_ref`, and `failure_ref`
are exact same-scope refs or `null` when that artifact does not yet exist;
their presence must match `event_kind` and `status`. `status` is one of the
envelope statuses in section 5.1, subject to the event kind. Allowed event
kinds are
`input_validated`, `raw_capture_started`, `raw_capture_committed`,
`parse_completed`, `registry_completed`, `mapping_completed`,
`composition_completed`, `checkpoint_written`, `interrupt_requested`,
`interrupted`, `cancelled`, `resumed`, `failed_closed`, and
`route_completed`.

The first event after input validation is raw capture start. A parser or
registry event without the corresponding raw capture event is invalid for a
generation node. Events are append-only; rewriting an earlier event or
reusing an event sequence fails replay.

### 10.3 Source exclusivity

Each record has one source kind and one source identity. `synthetic_fixture`,
`local_replay`, and `model_candidate` cannot be relabelled as one another;
`deterministic_projection` and `deterministic_composition` may consume only
validated upstream identities. A local replay cannot claim model/provider
success, and a scripted fixture cannot satisfy a future real-provider gate.
P4-01 has no real-provider source and cannot emit a real-provider success
record.

## 11. Checkpoint, interrupt, and resume

A future workflow checkpoint is an immutable reference record, not a second
domain state authority. Its payload has exactly `checkpoint_id`,
`graph_revision`, `last_event_seq`, `completed_node_ids`,
`pending_node_ids`, `interrupt_state`, `state_identity`, `contract_identity`,
and `resume_status`.

`checkpoint_id` and `graph_revision` are non-empty canonical ASCII strings;
`last_event_seq` is a non-negative integer that rejects boolean values.
`completed_node_ids` and `pending_node_ids` contain only frozen graph node IDs,
are duplicate-free and disjoint, and together cannot omit a topology node
whose execution state has been recorded. `state_identity` and
`contract_identity` are live-recomputed identity objects.

`interrupt_state` is `none`, `requested`, `paused`, `resumed`, or
`cancelled`; `resume_status` is `not_requested`, `eligible`,
`resumed_verified`, or `blocked`. Resume requires the same case, request,
contract, node revisions, source identities, prior event chain, and verified
raw captures. A checkpoint with missing/changed identity, an incomplete
registry, a changed advisory disposition, or a no-action flag drift is
`blocked` and routes fail closed. Interrupt/resume does not add a call or
authorize a retry. A cancelled or failed node cannot be resumed as a fresh
attempt without a separately approved action-time policy. `completed_node_ids`
and `pending_node_ids` must preserve frozen graph-topology order. `resumed` is
only a checkpoint/event state; it is never a `NodeResult` status. A retained
captured raw artifact from an interrupted/cancelled node remains non-success
evidence and cannot authorize registry, mapping, or composition.

LangGraph may own graph scheduling and checkpoint mechanics after P4-02 is
authorized. It may not mutate the canonical envelope, reinterpret a failure,
skip the registry, bypass Req2Web gates, or create a second workflow in
Agent Server/Studio or Coze.

## 12. One-call/no-retry and per-generation-consumer D17 gate

Each generation consumer--B-Aux, F1, F2, F3, and F4--has at most one model
call. P4-01 fixes every actual call count to `0`; a future consumer execution
may set its count to `1` only after that consumer's separate D17 action-time
approval. `b_aux_sidecar.call_count` is the B-Aux count, while
`NodeResult.call_count` is F1-F4-only and may not represent B-Aux. Automatic
retries are not allowed for any generation consumer. The global existing
one-repair rule is downstream deterministic repair authority and cannot be
used as a consumer retry or model-success label.

Each generation consumer must carry an exact D17 disposition payload with
`consumer_node_id`, `gate_status`, `manifest_ref`, `policy_ref`,
`input_view_ref`, `authorized_actions`, and `reason_code`.
`consumer_node_id` is one of `B-Aux`, `F1`, `F2`, `F3`, or `F4`.
The D17 record envelope `node_id` must equal `consumer_node_id`, and the
projection's `d17_disposition_ref` must resolve to that exact same-case/request
record.
`authorized_actions` is an exact object whose keys are `model_action`,
`runtime_execution`, `dependency_installation`, `training`, and `remote`, all
`false` in P4-01. `gate_status` is exact `not_approved_p4_01` and
`reason_code` is exact `p4_01_no_action`.

For P4-01, `manifest_ref`, `policy_ref`, and `input_view_ref` are all `null`;
no string sentinel may impersonate a Ref. A future revised action contract may
use `gate_status=approved` only when the three values are exact refs with
`ref_type=d17_manifest`, `ref_type=d17_policy`, and
`ref_type=d17_input_view` respectively, each bound to the same case, request,
and `consumer_node_id`. This exact-ref and same-scope rule applies equally to
B-Aux and F1-F4. Approval also requires a separately accepted action-time
amendment that binds the exact node prompt/input categories, runtime/profile,
model/revision, retention, cost, and cleanup identities. Any other gate status
or reason code requires a schema revision.

No historical one-call D17 authority, Qwen run record, or remote profile
automatically authorizes a generation consumer. A missing, stale, or cross-
consumer D17 record blocks that consumer's input and routes fail closed.

## 13. Acceptance layering and evidence boundary

The future chain has the following non-interchangeable layers:

1. B canonical requirement/use-case authority and optional advisory B-Aux.
2. F1-F4 raw/output records and deterministic registries.
3. Deterministic mapping and composition into a strict
   `ModelSemanticCandidate` candidate.
4. Existing parser/assembler and provenance/identity checks.
5. Independent `RequirementView`, `AcceptancePlan`, binding, and scripted or
   real browser Acceptance evidence under the existing gates.
6. G1/G2, one-repair, delivery package, and same-case frozen G0 fallback
   decisions.

An F4 candidate check, node status, registry status, or composed candidate is
not a RequirementView, AcceptancePlan, binding, browser observation, final
verdict, formal-quality result, H1 result, or evidence-use result. A failed or
unknown downstream gate blocks model success. Deterministic repair, closure,
and G0 delivery retain their existing labels and accounting.

## 14. C2D target decision

The C2D target remains the exact final `ModelSemanticCandidate` produced by
deterministic composition and then checked by the existing strict parser and
assembler. F1, F2, F3, and F4 outputs are node-local contract artifacts,
not an implicit training-data split and not a declaration that node-level
targets already exist.

The current data state is immutable for this slice:

```text
actual_pair_count=0
sealed_case_count=0
pair_authoring_paused_for_phase4_target_contract=true
```

P4-01 acceptance freezes the target decision but does not clear this pause or
authorize authoring. Resumption requires a separate data design decision and
owner approval.

Any future node-level target, prompt, annotation policy, train/dev split,
sealed-test rule, or assisted-authoring change requires a separate data
design decision, contamination/provenance review, and owner approval. The
24+40 plan is retained as a plan only.

## 15. Deferred decisions

The following are intentionally unresolved and are not implied by P4-01:

* Per-node D17 manifests, Provider-visible projections, prompts, model choice,
  runtime/profile, decoding, token/time/cost caps, and retention details.
* The LangGraph version and dependency set, graph persistence backend,
  checkpoint retention, stream schema, cancellation timeout, and Studio API
  surface.
* Whether B-Aux will be used by any future F node after its own D17 review;
  the four disposition values remain mandatory regardless.
* Node-level training targets, LoRA data construction, review roles, and
  train/dev/sealed-test material.
* Local model execution, Provider/backend/service connection, remote/GPU/
  AutoDL execution, result return, release, revocation, H1/gold, browser
  acceptance, formal quality, and publication claims.

## 16. P4-01 acceptance checklist

This checklist was closed by the manager and the independent reviewer for the
accepted local no-model contract boundary:

- [x] P4-00/P4-01 names and the unique alias/supersession table are synced in
      all ten existing entry documents.
- [x] H1/H1-gold/Path 3 non-H1 meanings are explicitly preserved; historical
      M3/M4/M5 facts are not rewritten.
- [x] The exact no-action declaration is present at packet and record-class
      scope, with all five values false.
- [x] Authority hierarchy and the B/B-Aux boundary are exact;
      `b_aux_sidecar` has the sole exact advisory schema, actual `call_count`,
      status/failure, and live-recomputed sidecar-hash contract, never receives
      stable IDs, and cannot write back to B.
- [x] The four per-F advisory dispositions bind the exact sidecar identity,
      consumer node, D17 state, and frozen reason code without authorizing
      sidecar use in P4-01.
- [x] B-Aux and every F `node_input_projection` have the exact consumer-
      specific logical/prohibited class order, refs, upstream and mapping
      identities; B-Aux has empty upstream identities and null mapping/advisory
      refs, while every consumer has its own no-action D17 Ref and no Provider
      payload or inherited one-call D17 authority.
- [x] Common envelope, exact keys, refs, identity domains, canonicalization,
      raw-first capture, and replay rules are independently checked.
- [x] F1/F2/F3/F4 output schemas reject extra/missing keys and define local
      refs, failure semantics, and the F4 acceptance-layer boundary.
- [x] Stable-ID registry, deterministic mapping, deterministic composition,
      source exclusivity, B-Aux/F1-F4 one-call/no-retry counts and D17 gates,
      failure routing, and checkpoint/resume rules are independently checked.
- [x] B-Aux advisory raw order, B-Aux/F input/prohibited-class order, F
      semantic order, registry order, mapping order, event order, and
      checkpoint topology order all fail closed on drift.
- [x] C2D target and zero-count authoring pause are unchanged.
- [x] Only the allowed docs are changed; no code, dependency, model, data,
      training, GPU, remote, staging, commit, or push action occurs.
- [x] Required docs-only checks pass: target-reference existence,
      UTF-8/no-BOM, and `git diff --check`.

### Acceptance evidence (2026-08-02)

The same Luna-max exact-diff final review reported `P0=0`, `P1=0`, `P2=0`,
`review_acceptance=yes`, and `additional_scheme_adjustment_required=no`. Two
predecessor managers confirmed that the P4-01 constraints/edges/profile
closure requires no additional owner scheme. Manager static evidence recorded
11 target files as UTF-8 valid with no BOM, 24 changed/new references with
zero missing, and `git diff --check` exit 0. No code test, model action, or
runtime action is acceptance evidence for this docs-only contract.

## 17. P4-02 stop gate

The required post-P4-01 state is:

```text
p4_01_status=accepted_local_no_model_contract
p4_02_authorized=false
langgraph_dependency_installation=false
runtime_implementation=false
model_action=false
training=false
remote=false
```

With `p4_01_status=accepted_local_no_model_contract`, the manager must stop and
report. P4-02 requires a separate owner decision.
P4-01 is not a dependency-installation, LangGraph-runtime, model, training,
remote, GPU/AutoDL, H1, or formal-quality authorization.

## 18. Controlling sources

The historical source and current control relationship is maintained in:

* `docs/stage3_agent_chain_orchestration_amendment.md` (historical M3-AC
  source boundary plus current Phase 4 supersession note).
* `docs/stage3_solution_decision_register.md` (D20 source decision and alias
  control).
* `docs/stage3_project_manager_handoff.md` (historical handoff and current
  Phase 4 manager boundary).
* `docs/stage3_runtime_profile_architecture.md` (historical profile boundary;
  no runtime authorization).
* `docs/stage3_lora_gate_c2d_human_authoring_workspace.md` and its operator
  guide (zero-sample C2D pause).

Those historical documents remain authoritative for the facts they recorded;
this packet is the accepted local no-model contract for P4-00/P4-01 naming and
scope. No source permits work beyond the explicit P4-01 docs-only boundary.
