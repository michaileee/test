# Code signing policy

Status: not enrolled, current releases unsigned. The proposed public project is
named Procurement Registry. A SignPath Foundation application is planned; approval
is not guaranteed. This document does not claim a certificate has been issued.

After acceptance, the attribution required by the program will be published here:
"Free code signing provided by SignPath.io, certificate by SignPath Foundation".
It is conditional until the foundation approves the project and signing succeeds.

## Maintainers and approvals

- Repository owner and committer: [@michaileee](https://github.com/michaileee).
- Reviewer: [@michaileee](https://github.com/michaileee), subject to enrollment review.
- Signing approver: [@michaileee](https://github.com/michaileee); every release requires manual approval.

All repository and signing accounts must use multi-factor authentication. Changes
from external contributors require review. Only our own launcher is submitted for
signing. Unmodified upstream CPython binaries are included as third-party components
and are not re-signed using the project's signing identity.

## Build and artifact rules

Releases are built from a specific public source revision using the versioned
build scripts and locked downloads. The Windows CI smoke test must pass first.
The product name is `Procurement Registry`, the initial product version is
`3.0.0.0`, and the submitted filename is `ProcurementRegistry.exe`.

The signing job is disabled until SignPath approval, repository roles, artifact
configuration, and GitHub secrets/variables are set. It runs only from the main
branch on a manual request, through the protected `code-signing` environment.
No signing token or private key is stored in source. Signed artifacts must pass
Windows Authenticode verification before being published as signed releases.

Sources: https://signpath.org/terms.html and
https://docs.signpath.io/trusted-build-systems/github .
Privacy: [PRIVACY.md](PRIVACY.md).
