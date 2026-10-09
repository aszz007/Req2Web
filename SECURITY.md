# Security Policy

## Project status

Req2Web is a research prototype and is not currently supported as a production
service. The repository is pre-release. Project-authored software is licensed
under Apache-2.0; external material is not relicensed, and the full development
repository remains private pending a sanitized publication decision. See
[LICENSING.md](LICENSING.md).

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

## ResultPackage previews

A valid package inventory proves byte integrity, not that its HTML or
JavaScript is trustworthy. The Inspector serves result-package and historical
replay files with a response-level Content Security Policy sandbox. Preview
scripts can update their own page but do not retain the Inspector origin,
cannot use its storage, and cannot fetch its local API. Network connections,
form submission, nested frames, workers, and privileged browser features are
restricted. This also applies to direct preview links opened in a new tab.

The Inspector controls and explicit ZIP download remain available. Preview
pages that require a backend, browser storage, external assets, or popups are
intentionally unsupported in this isolated view. Package bytes, identities,
and historical experiment evidence are not rewritten. Downloaded packages
opened outside the Inspector do not receive these response headers: inspect
their source and run untrusted content in an appropriately isolated environment.

This is a local research-tool boundary, not a production-service security
guarantee or a general malware detector.

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
