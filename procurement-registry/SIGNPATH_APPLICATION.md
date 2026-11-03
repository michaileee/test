# SignPath Foundation application draft

Submission status: not submitted. Complete the account-specific fields after
the public repository and its first Windows-tested release are available.

- Project name: Procurement Registry
- Repository: https://github.com/michaileee/test
- Release/download page: Pending successful Windows CI and release publication
- Maintainer contact: Repository owner: @michaileee; contact supplied privately in the application
- Committer, reviewer, approver identities: @michaileee (repository owner; MFA must be confirmed before enrollment)
- Project license: MIT; dependency licenses retained separately
- Artifact: Windows x64 `ProcurementRegistry.exe`, product name Procurement Registry
- Code signing policy: link to CODE_SIGNING_POLICY.md in the published repository
- Privacy policy: link to PRIVACY.md in the published repository

## Description for the application

Procurement Registry is an unofficial Windows tool that exports public tender and
CMR procurement metadata from Georgia's State Procurement Agency website. Users
provide buyer identification codes and years, then obtain two local Excel files.
It performs only user-requested read-only queries over HTTPS and has no analytics,
autorun service, privilege escalation, or security-setting changes. The interface
is local to 127.0.0.1. Runtime and dependency files are included in one executable,
extracted into the user's application-data directory, and hash-verified before use.

The launcher is built from our own C source using a pinned compiler. The embedded
CPython runtime is an unmodified official upstream distribution with a pinned
archive hash. Python dependencies are exact-version and SHA-256 locked. We request
signing of our own outer executable, not re-signing upstream Python binaries.

The repository has a Windows build/smoke workflow, an explicit privacy policy,
license notices, and a proposed manual-approval signing policy. Enrollment and
project reputation requirements still need the foundation's review.

## Before submitting

1. Publish the clean source repository and fill in real project/account links.
2. Run Windows CI and resolve any native startup or cache-repair failure.
3. Publish an unsigned, clearly labeled tested release; enable MFA for maintainers.
4. Assign the committer/reviewer/approver roles and obtain their agreement.
5. Apply using the current form at https://signpath.org/apply.html .
6. If accepted, configure the artifact restrictions, GitHub trusted-build origin,
   signing policy, protected environment, and secrets/variables described in the workflow.
7. Request signing; verify the resulting Authenticode signature before distributing it.
