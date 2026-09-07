# Windows binary trust contract

## Runtime and product metadata

`SubtitleEditBayLauncher.exe` is compiled with MSVC `/MT`, so the launcher that reaches setup/repair does not require a separately installed Visual C++ runtime. The build embeds a VERSIONINFO resource containing ProductName, FileDescription, FileVersion, ProductVersion and CompanyName. `verify_windows_binary.ps1` checks these fields and uses `dumpbin /DEPENDENTS` to reject dynamic VC/UCRT imports.

The resource compiler accepts an explicit `.ico` path. An approved product icon asset is not yet present in the repository, so icon selection remains an external design input and is not silently replaced with an arbitrary icon.

## Authenticode contract

Formal distribution requires SHA-256 Authenticode signatures on both the launcher before it is packed and the final installer, plus a trusted timestamp and the expected publisher subject. `sign_windows_artifacts.ps1` loads a base64 PFX and password from process environment only, requests a timestamp, and immediately verifies signer and timestamp. It does not write the certificate to the workspace or artifact.

`verify_windows_binary.ps1 -RequireSignature -RequireTimestamp` and the updater-side `Assert-InstallerPublisher` implement independent verification. The GUI update helper checks checksum first, then signer subject and timestamp, before snapshot creation or process execution.

## Trust boundary and current blocker

PR-controlled workflows must not receive a repository signing key. The current pre-merge artifact promotion architecture therefore cannot safely consume a normal repository secret: a same-repository PR can modify the workflow or signing script before secret use. No managed signing provider, certificate subject, protected signing environment or OIDC policy has been selected or provisioned.

Until that trust boundary is supplied, VERSION-only release preparation sets `require_signature: true` and fails before artifact upload with an explicit configuration error. Infrastructure PRs can still exercise unsigned build, runtime dependency and resource checks, but an unsigned formal candidate cannot become publishable.

To unblock signing, choose a managed signing provider or protected workflow whose policy pins trusted workflow code and candidate digest. Then connect the provided signing primitive in this order:

1. build launcher, verify imports/resources, sign and verify launcher;
2. build installer containing that exact launcher;
3. sign and verify installer with timestamp;
4. generate checksum/manifest only after signing;
5. install/start that same signed installer on a clean Windows environment;
6. record publisher, certificate thumbprint and timestamp evidence in the immutable preparation manifest.

The certificate/provider, approved icon and genuinely clean Windows VM are external prerequisites. GitHub-hosted `windows-latest` contains development runtimes and is suitable for import-table enforcement, but not proof of a clean-PC environment.
