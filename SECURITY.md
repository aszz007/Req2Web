# Security

Req2Web is a research prototype, not a production service. Run the Inspector
on loopback only. The public repository excludes private datasets, operational
records, model weights, and historical result payloads.

## Reporting a problem

Contact the repository owner privately with the affected commit and a small,
safe reproduction. Do not post credentials or private data in public issues.
Only the current default branch is considered for fixes.

## Preview safety

Package hashes verify integrity, not whether HTML or JavaScript is trustworthy.
Inspector previews use an opaque-origin sandbox that blocks access to its API,
storage, and external network while permitting local scripts and form interaction.
Downloaded files opened elsewhere do not inherit these restrictions. Inspect
untrusted content before running it; the sandbox is not a malware detector.

## Before pushing

Run `python scripts/audit_repository_secrets.py --source index` for staged files
or use `--source history` for locally reachable commits. The check is heuristic
and never tests credential validity or sends content to a detection service.

Do not commit credentials, private data, model files, or generated results.
If a real credential is exposed, revoke or rotate it; deleting a file alone
does not remove its history. Never test or revoke another person's credentials.

See [LICENSING.md](LICENSING.md) for the scope of the Apache-2.0 license.
