# UI form_structure compact signal adapter

## Scope

This is the single follow-up from `ui_reference_conversion_review.md`. The
adapter runs only when an already-loaded `ui_reference` RAG document becomes a
compact result, and Guidance only reads and revalidates that optional field. It
does not open UI assets, hierarchy, the corpus, or `data/raw`; it does not
change retrieval, PageSpec v1, Renderer, Consistency, Influence, or
result-package schemas.

## Contract

`ui_structure_signals` is absent for non-UI documents and may be absent from
old UI compact results. Its sole candidate carries `signal_id`, `outcome`,
`value`, `source_doc_id`, `source_fields`, `source_values`, `reference_uris`,
and `adapter_rule`. It exists only when `metadata.category` is one of
`form_input`, `login_auth`, or `settings`, the same document has a positive
integer `metadata.component_labels.Input`, and it has existing whitelisted UI
references. The stable ID hashes the parent document, source values, URI list,
and finite rule.

Guidance recomputes that contract from compact fields. An altered ID, parent
document, source fields/values, URI set, rule, outcome, or value is not a
candidate. It falls back to the pre-existing `reference_screen` and URI
evidence behavior. URI is provenance only, never a component trigger.

## Controlled adoption

`form_structure` is the only new controlled UI value. It passes only when the
original requirement, use cases, or explicit constraints clearly say a form
action: `填写`, `表单`, `登录`, `注册` (or bounded English equivalents). “设置”,
“按钮”, or “输入” alone do not pass. Adoption reuses `component_type=form` and
records the component purpose as the actual affected field; it makes no
field-name, submission, authentication, social-login, password, or recovery
inference. Duplicate candidates keep the first compact result's stable source.

## Verification

`tests/test_ui_form_structure_adapter.py` covers source conjunctions,
whitelisted references, malformed/tampered signals, old compact compatibility,
stable duplicates, context gates, and no case-specific constants. Full Demo v2
is rebuilt twice and compared under ignored `outputs/`.
