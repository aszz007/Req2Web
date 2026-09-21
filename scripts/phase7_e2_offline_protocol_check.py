"""Check chat EOS and historical framing failures offline; never load weights."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase7_e2_diagnostic_runtime import generation_stop_ids, PROTOCOL_REVISION


def check(model_root: Path, historical_run: Path, output: Path) -> dict:
    from transformers import AutoTokenizer
    from transformers.generation.stopping_criteria import EosTokenCriteria
    import torch

    tokenizer = AutoTokenizer.from_pretrained(str(model_root), local_files_only=True, trust_remote_code=False)
    config = json.loads((model_root / "config.json").read_bytes())
    model_eos = config.get("eos_token_id", config["text_config"]["eos_token_id"])
    ids = generation_stop_ids(tokenizer, model_eos)
    last_chat_token = torch.tensor([[1, tokenizer.eos_token_id]], device="cpu")
    old_stops = bool(EosTokenCriteria(model_eos)(last_chat_token, None)[0])
    corrected_stops = bool(EosTokenCriteria(ids)(last_chat_token, None)[0])
    if old_stops or not corrected_stops:
        raise ValueError("the expected historical EOS mismatch was not reproduced")
    rows = []
    summary = json.loads((historical_run / "summary.json").read_bytes())
    for session in summary["sessions"]:
        files = sorted((historical_run / session["session_id"]).glob("*.raw"))
        path = files[-1]
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        first, end = json.JSONDecoder().raw_decode(text)
        rows.append({"session_id": session["session_id"], "raw_sha256": hashlib.sha256(raw).hexdigest(),
                     "first_object_action": first.get("action"), "trailing_content_bytes": len(text[end:].strip().encode()),
                     "trailing_user_role": text[end:].lstrip().startswith("user"),
                     "historical_status": session["status"]})
    result = {"protocol_revision": PROTOCOL_REVISION, "model_loaded": False, "model_calls": 0,
              "model_default_eos": model_eos, "chat_eos": tokenizer.eos_token_id,
              "corrected_eos_ids": ids, "old_stops_at_chat_eos": old_stops,
              "corrected_stops_at_chat_eos": corrected_stops, "historical_sessions": rows,
              "scope": "CPU stopping-criterion reproduction and read-only raw framing audit; no answer recovery or model quality claim"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-root", type=Path, required=True)
    p.add_argument("--historical-run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(check(a.model_root, a.historical_run, a.output), indent=2))
