# Procurement Registry

An unofficial Windows application for exporting public Georgian procurement records.
It retrieves tender and CMR metadata for buyer identification codes and years chosen
by the user, then creates two consolidated Excel files. It is not affiliated with
the State Procurement Agency and does not provide a legal compliance opinion.

## გამოყენება

Windows 10/11 x64-ზე გაუშვით `ProcurementRegistry.exe`.
Python-ის, pip-ის ან ბიბლიოთეკების ცალკე დაყენება საჭირო არ არის.
პირველ გაშვებაზე აპლიკაცია ამზადებს საკუთარ ფაილებს მომხმარებლის საქაღალდეში.
შემდეგ გაშვებებზე ფაილები მოწმდება SHA-256-ით და გამართული ასლები ხელახლა გამოიყენება.
დაზიანებული ან დაკარგული კომპონენტი ჩაშენებული ასლიდან აღდგება.

ინტერფეისი იხსნება არსებულ ბრაუზერში. მიუთითეთ ერთი ან რამდენიმე საიდენტიფიკაციო
კოდი და წლები. დასახელებები, დეაქტივირებული ჩანაწერების ჩათვლით, საიტიდან მოიძებნება.
მონაცემების მიღებას სჭირდება ინტერნეტი. დასრულების შემდეგ გამოიყენეთ
„აპლიკაციის დახურვა“ და დახურეთ ბრაუზერის ჩანართი.

ტენდერებისა და CMR-ის შედეგები ორ საერთო Excel-ში ინახება. რამდენიმე ორგანიზაციის
შესყიდვები უკავშირდება საიდენტიფიკაციო კოდით, სისტემური ID-ითა და შესყიდვის ნომრით.
დოკუმენტების შიგთავსის ავტომატური წაკითხვა ამ ვერსიაში არ შედის.

## Data and network behavior

Only user-requested read-only queries are sent to `tenders.procurement.gov.ge` over
HTTPS. The interface is served on `127.0.0.1` and uses an unpredictable request token.
There is no analytics, advertising, auto-start service, registry installation, or
automatic upload of exported files. See [Privacy](PRIVACY.md).

The application runs without administrator rights. Local files are stored under:

- `%LOCALAPPDATA%\ProcurementRegistry\cache\<build-id>`: verified runtime and code.
- `%LOCALAPPDATA%\ProcurementRegistry\exports`: persistent exports across app updates.
- `%LOCALAPPDATA%\ProcurementRegistry\startup_error.log`: startup failures, if any.

To remove it, close the app, delete the EXE and the cache directory. Export files
are user data: keep them or delete them separately. No service or registry cleanup
is required. Do not turn off Windows protections to run the application; managed
devices may require approval from the organization's administrator.

## Performance and completeness

Detail requests use at most two workers, with a separate persistent HTTP session
for each worker. Tender detail tabs, bid histories, and supplier profiles reuse
these sessions. Search result pages remain sequential within their original session.
Page numbers, total counts, and unexpected duplicate IDs are checked. Legitimate
independent payment rows with matching dates or amounts are retained.

Transient disconnects, timeouts, and HTTP 429/500/502/503/504 responses are retried
every 30 seconds until recovery. TLS certificate failures and unexpected response
structures are reported instead of being ignored. Progress survives a connection
interruption in memory; restart-after-crash recovery is not implemented.

Calendar-year filtering is source-specific. These exports are not a guaranteed
complete ledger of payments on all older contracts. See the period-related fields.
Speed depends on the procurement website; no fixed speedup is promised.

## Build from source

Build host: x64 Linux or Windows, Python 3.12+ and network access for the official
CPython runtime, locked PyPI wheels, and the pinned Zig compiler. These are build
requirements only. The downloaded compiler and runtime are not installed globally.

```console
python build_single_file.py
python verify_single_file.py
```

Output: `outputs/single_file/ProcurementRegistry.exe`.
The MSZIP cabinet is linked into a regular PE section, so later Authenticode
signing does not change the payload lookup. All payload files are hash-checked
before execution. The loader uses Windows Cabinet.dll and BCrypt; no PowerShell,
tar, external extractor, administrator prompt, or security-setting change is used.

Dependencies are exact-version and hash locked in
`procurement_app/requirements-portable.lock.txt`. The Windows runtime archive is
SHA-256 pinned. The project MIT license applies to project-authored code; bundled
dependencies retain their own licenses. See [Third-party notices](THIRD_PARTY.md).

## Testing

The repository-root `.github/workflows/procurement-registry.yml` builds on a Windows runner, runs the source tests,
then executes the final EXE with `--self-test`. A second invocation checks cache
reuse; a deliberately damaged asset checks automatic repair. Native Windows
execution remains unverified until this workflow or a Windows user completes it.
The Linux build checks PE metadata, every cabinet member with two decoders,
dependency imports, certificates, local HTTP, and data-folder separation.

For a manual Windows startup check:

```powershell
$p = Start-Process .\ProcurementRegistry.exe -ArgumentList '--self-test' -Wait -PassThru
$p.ExitCode
```

Exit code 0 indicates success. Diagnostic details are written to
`%LOCALAPPDATA%\ProcurementRegistry\startup_diagnostics.json`.

## Code signing policy

The current EXE is **unsigned**. No SignPath acceptance or trusted signature is
claimed. The repository includes an approval-gated signing workflow and an
[application draft](SIGNPATH_APPLICATION.md). Maintainer identities and repository
URLs must be completed before applying. See [Code signing policy](CODE_SIGNING_POLICY.md).

## License and contributions

Project-authored code is licensed under MIT. Changes should retain complete-page
validation, TLS verification, request bounds, and independent payment rows.
Report a reproducible bug with the app version and a redacted diagnostic log.
Never attach credentials or private exports to a public issue.
