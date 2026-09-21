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
