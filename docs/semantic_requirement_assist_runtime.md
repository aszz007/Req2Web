# Semantic Requirement Assistant Runtime

Status date: 2026-08-11.

## Purpose and isolation

The Inspector can optionally ask the same Qwen3.5-9B family used by the Phase
4 generation runtime to review the deterministic requirement understanding.
It returns a short advisory list of ambiguity, conflict, missing information,
risk, and suggestions. The advice is a separate sidecar: it cannot rewrite
canonical B, cannot enter F1-F4, cannot block deterministic draft creation,
and cannot change a ResultPackage or any frozen Phase 5 result.

The repository server keeps this capability disabled unless an operator passes
the explicit local-model flags. The extracted reviewer bundle never loads a
model. A provider-neutral closed-API capability is declared as
`reserved_not_connected`; it accepts no credentials and implements no network
call.

## Execution profiles

Both profiles use Qwen3.5-9B revision
`c202236235762e1c871ad0ccb60c8ee5ba337b9a`, deterministic decoding, SDPA,
complete-JSON stopping, raw-first capture, one call, and zero automatic retry.
Neither profile truncates the input or treats an incomplete response as a
success.

| Profile | Intended hardware | Precision | Output cap | Claim boundary |
| --- | --- | --- | --- | --- |
| `local_low_gpu_nf4` | One CUDA GPU with at least 8 GB total and 5.5 GB free VRAM | NF4 with double quantization and BF16 compute | Six advisory items, 1,280 new tokens | Local compatibility and development evidence only |
| `high_gpu_bf16` | One CUDA GPU with at least 30 GB total and 24 GB free VRAM | BF16, no quantization | Six advisory items, 2,048 new tokens | Eligible for a later separately authorized quality study; not quality evidence by itself |

CPU offload is disabled because it can hide an unsupported GPU profile and
make completion timing less predictable. A model inventory check and a GPU
memory preflight occur before model loading. The worker records its current
stage, saves raw bytes before parsing, and returns an exact failure location on
timeout, OOM, runtime failure, or contract rejection.

## Local compatibility evidence

The first bounded RTX 4060 Laptop 8 GB run used the original twelve-item,
2,048-token local output limit. It completed without OOM, captured 7,827 raw
bytes, and used no retry. The strict validator rejected the sidecar because the
last two items reversed the required tuple order of their canonical-B
references. The failure remains immutable and is not relabeled as success.

The revised local profile selected at most six high-value items, reduced the
local token cap to 1,280, and made the reference ordering check explicit. A
separate one-call validation on the same GPU then produced six accepted
advisory items. It used 641 input tokens and 732 output tokens, captured 2,789
raw bytes, reserved 8,287,944,704 CUDA bytes at peak, and completed with zero
OOM, timeout, truncation, normalization, repair, or retry. GPU memory returned
to the normal desktop baseline after worker exit.

This proves that the low-memory profile can complete one representative
irregular English requirement on the inspected 8 GB machine. It does not prove
general semantic quality, broad robustness, high-GPU behavior, formal quality,
H1/gold performance, or production readiness. The high-GPU profile is
implemented and statically tested but was not executed on this machine.

## Operator commands

Inspect the provider states without loading a model:

```powershell
python scripts/run_semantic_requirement_assist.py --show-providers
```

Enable the assistant in the repository Inspector:

```powershell
python scripts/run_req2web_inspector.py `
  --enable-local-semantic-assist `
  --semantic-profile local_low_gpu_nf4 `
  --semantic-model-root path/to/exact/Qwen3.5-9B `
  --semantic-integrity-evidence path/to/integrity.json
```

Select `high_gpu_bf16` only on a machine that satisfies its preflight. The
model is loaded lazily after the user presses **Run semantic assistant**. The
deterministic requirement check and draft controls remain available if the
assistant fails closed.

## Evidence identities

The failed format-strict run is `assist-b7ba60d20d69`; its raw SHA-256 is
`0a998e77cd1e168f44791d001e9d568c023724022ec6bf31a85c9fd0b430e91d`.
The accepted revised-profile run is `assist-eb6e2e548a56`; its raw SHA-256 is
`0786bd717e73e9d39e0db0e6929219e94258632e414934bdceadd7405705039f`.
These two semantic-assistant calls are a new local development ledger and do
not modify the historical Phase 5 ledgers of 48 generation calls and twelve
semantic-acceptance calls.
