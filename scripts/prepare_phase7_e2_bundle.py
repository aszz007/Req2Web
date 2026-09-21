"""Create a separate E2 upload candidate; never rewrite the E1 payload.

Reuse only the verified E1 runtime/source inventory, then include explicitly
allowlisted E2 code and public packets. Private scoring labels stay local.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import prepare_phase7_autodl_bundle as archive_api
from phase7_e2_diagnostic_packets import validate
from phase7_e2_diagnostic_runtime import validate_public_root


def build(preparation, base_payload, destination):
    validate(preparation)
    receipt=json.loads((preparation/"preparation.json").read_bytes())
    validate_public_root(preparation/"public",receipt["public_manifest_sha256"])
    handoff=json.loads((base_payload/"handoff_manifest.json").read_bytes())
    archive=base_payload/handoff["archive_filename"]
    archive_api.validate_archive(archive,handoff["archive_sha256"])
    files={}
    with zipfile.ZipFile(archive) as z:
        base=json.loads(z.read("payload_manifest.json"))
        for row in base["files"]:
            path=row["path"]
            if path.startswith("src/") or path in {"runtime/model_integrity.json","runtime/requirements-model.txt","runtime/runtime_profile.json","requirements-phase4-agent-lock.txt"}:
                files[path]=z.read(path)
    explicit=("scripts/phase7_e2_diagnostic_runtime.py","scripts/phase7_e2_supervise.py",
              "scripts/prepare_phase7_autodl_bundle.py","fixtures/phase7_e2_action.template.json")
    for path in explicit:files[path]=archive_api.checked_bytes(ROOT/path)
    public_manifest=json.loads((preparation/"public/manifest.json").read_bytes())
    files["e2/public/manifest.json"]=(preparation/"public/manifest.json").read_bytes()
    for packet in public_manifest["packets"]:
        for row in packet["files"]:
            relative=f"packets/{packet['packet_id']}/{row['path']}"
            files["e2/public/"+relative]=archive_api.checked_bytes(preparation/"public"/relative)
    metadata={"purpose":"E2 diagnostic assistance preparation only; independent of E1",
              "source_commit":base["source_commit"],"base_E1_archive_sha256":handoff["archive_sha256"],
              "public_manifest_sha256":receipt["public_manifest_sha256"],
              "preparation_sha256":archive_api.sha((preparation/"preparation.json").read_bytes()),
              "builder_sha256":archive_api.sha(Path(__file__).read_bytes()),
              "native_source_policy":"reuse exact verified E1 source inventory; no new production writes",
              "model_selection":"Qwen3.5-9B BF16 adapter candidate; future owner runtime approval required",
              "excluded":["private gold","case-to-family mapping","local native source outputs","RAG documents","raw datasets","credentials","model weights","E1 experimental inputs and outputs"],
              "measured_sessions_executed":0,"runtime_selected_or_approved":False}
    return archive_api.write_archive(files,destination,metadata)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preparation",type=Path,required=True)
    p.add_argument("--base-payload",type=Path,default=ROOT/"outputs/phase7_autodl_prep_v1/payload-final-20260914")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.preparation,a.base_payload,a.output),indent=2))
