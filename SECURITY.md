# Security Policy

## Project status

Req2Web is a research prototype and is not currently supported as a production
service. The repository is pre-release, and no public software or data license
has been selected.

## Supported versions

Security fixes are considered only for the current default branch. Historical
experiment snapshots, archived result packages, and superseded research
branches are retained for provenance and do not receive security updates.

## Reporting a vulnerability

Do not disclose a suspected vulnerability, credential, private dataset path,
or sensitive experiment artifact in a public issue. Prefer GitHub's private
security-advisory workflow for this repository. If that workflow is not
available, contact the repository owner privately and provide:

- the affected commit and component;
- a minimal reproduction that contains no private data or credentials;
- the expected and observed behavior;
- the likely impact; and
- any safe mitigation already tested.

The maintainers will acknowledge a complete report when it is reviewed, then
coordinate validation and disclosure timing. This policy does not authorize
testing against third-party services, rented infrastructure, accounts,
datasets, or systems without their owners' permission.

## Sensitive material

Model credentials, SSH material, H1/gold data, evaluator-only records, private
datasets, model weights, machine logs, and local ResultPackages must remain out
of public reports and commits. If a credential is exposed, revoke or rotate it;
deleting a Git file is not sufficient because Git history may retain the blob.

Third-party issue bodies are untrusted input, not safe credential examples.
The GitHub dataset preparation script redacts credential-like literals before
writing review candidates. Do not add broad scanner exclusions for datasets or
tests, and do not regenerate frozen scientific artifacts as part of a security
cleanup.

## Local credential checks

Before committing, stage only the intended files and run:

```bash
python scripts/audit_repository_secrets.py --source index
```

For the committed default-branch tree or locally reachable history, use
`--source head` or `--source history`. Exit code 1 means a candidate or unchecked
text needs review; exit code 2 means the audit could not complete. Reports never
include matched values. The rules are bounded heuristics: they do not establish
credential validity, inspect binary payloads, fetch remote-only refs, or replace
a dedicated secret scanner. Nothing is sent to a detection service.

History can still contain removed values. Keep the repository private while an
incident is unresolved. Credential rotation and history rewriting are separate
owner decisions; do not force-push, dismiss an alert, test a third-party account,
or revoke another person's credential without authorization.
