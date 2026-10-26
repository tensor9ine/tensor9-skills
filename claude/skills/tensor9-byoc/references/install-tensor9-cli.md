# Installing the public Tensor9 CLI

Use this reference when the public `tensor9` command is missing or unusable. The public
[installation documentation](https://docs.tensor9.com/byoc/cli/install) describes supported methods.
A Prime checkout, private `t9dev`, and a global Java installation are not required: the public
bundle contains its launcher, CLI JAR and Java runtime.

## Check first and agree on installation

Before executing any CLI or catalog helper command, resolve `tensor9` using command lookup without
running it. Require an absolute executable file path; follow all symlinks to its real path. Reject
both a discovered path and a resolved target inside the current working directory or the vendor
workspace. Do not invoke an alias, shell function or repository wrapper as the CLI. The catalog
loader rejects an executable beneath its working directory, but this initial check must happen
before any version command. If no acceptable executable exists, treat the CLI as missing.

Set `tensor9_cli` to the verified absolute path. Then run the public `tensor9 version` command
through that path, and prepend its checked external directory to `PATH` before invoking the
bundled catalog helper so its lookup selects the same CLI:

```sh
"$tensor9_cli" version
```

If it works, keep that installation. If it fails, report the actual failure; it does not by itself
justify replacing or upgrading the CLI. Determine the OS and architecture (`uname -s` and `uname -m`
on macOS/Linux). Supported bundles are macOS and Linux on arm64/aarch64 or amd64/x86_64. On Windows,
use an existing supported Linux environment such as WSL; native Windows installation is not established
by these instructions. Do not install WSL or system prerequisites without separate consent.

Before installing, explain the selected method, download source and destination, then obtain the
vendor's consent. For Homebrew this includes adding/trusting the Tensor9 tap and Homebrew-managed
files. For the shell installer, agree on an absolute private prefix outside the vendor workspace;
use a new directory whose ancestors are real directories, not symlinks. Installation consent is
separate from annotation, integration or stack-change decisions. If declined or blocked, use the
bundled catalog and report "refresh not performed" with the fallback reason.

Do not automatically upgrade an existing CLI, log in, inspect credentials, change a profile,
run vendor setup, compile, publish or deploy. A missing profile or authentication error after
installation is an open prerequisite, not permission to create accounts or select credentials.

## macOS with existing Homebrew

For a vendor who chooses Homebrew, first read `brew --version`. The public tap is
[tensor9ine/homebrew-tensor9](https://github.com/tensor9ine/homebrew-tensor9).
After consent, add it:

```sh
brew tap tensor9ine/tensor9
```

On Homebrew 7 or later, trust the tap before installation:

```sh
brew trust tensor9ine/tensor9
```

Older Homebrew versions do not support `brew trust`; omit that command there. Then install:

```sh
brew install tensor9
```

Do not install or upgrade Homebrew automatically. If it is absent or the package is unavailable,
offer the shell installer below. Do not use `brew upgrade tensor9` to recover from a catalog error.

## macOS or Linux with the public shell installer

The public installer is
[install-latest.sh](https://t9-artifacts-prod-1.s3.us-west-2.amazonaws.com/install-latest.sh).
It downloads the current platform-specific bundle. It uses HTTPS but does not itself verify an
archive checksum or signature; do not claim that it does. These latest downloads are mutable.
If the vendor requires a fixed release or independently verified digest, resolve that requirement
before installation rather than inventing a verification result.

The installer needs `sh`, `tar`, and `curl` or `wget`. This example uses `curl`. After agreeing on
the destination, set `tensor9_install_prefix` to that absolute path. The example below is suitable
only if it is outside the workspace, has no symlink ancestors, and is the approved destination.
Do not overwrite an existing installation as part of this missing-CLI flow.

```sh
tensor9_install_prefix="$HOME/.local/share/tensor9-cli"
tensor9_installer_dir="$(mktemp -d)"
curl --fail --location --proto '=https' --tlsv1.2 \
  https://t9-artifacts-prod-1.s3.us-west-2.amazonaws.com/install-latest.sh \
  --output "$tensor9_installer_dir/install-tensor9.sh"
```

Inspect the downloaded installer before running it. Execute only after the agreed installation
consent. Setting `T9_INSTALL_DIR` chooses the private prefix; including its `bin` directory in
`PATH` for the installer prevents it from editing shell startup files. If the legacy
`TENSOR9_INSTALL_DIR` is already set to a different destination, stop and explain the conflict
rather than changing that setting silently.

```sh
env T9_INSTALL_DIR="$tensor9_install_prefix" \
  PATH="$tensor9_install_prefix/bin:$PATH" \
  sh "$tensor9_installer_dir/install-tensor9.sh"
export PATH="$tensor9_install_prefix/bin:$PATH"
```

The `export` applies to this shell session. Persistent PATH changes require agreement on the
specific startup file. Do not use `sudo` to work around an unapproved destination. If installation
fails, retain the error and any recovery paths reported by the installer; do not delete an
installation lock or recovery directory automatically. Remove only the temporary installer file
and its temporary directory when they are no longer needed.

## Verify, then return to your BYOC task

Check the installed CLI and whether this version exposes the catalog command:

Resolve and check the installed executable again as above, then use its verified absolute path
for `tensor9 version` and `tensor9 prepare considerations --help`:

```sh
"$tensor9_cli" version
"$tensor9_cli" prepare considerations --help
```

Help or version output alone does not establish successful authentication or a catalog refresh.
Return to [catalog access and vendor decisions](considerations-usage.md) and use the bundled loader
with the vendor's existing profile selection. It invokes only
`tensor9 prepare considerations -snapshot -json` for metadata. Report the loader's actual
provenance, refresh outcome and any fallback reason. Missing credentials, unsupported commands,
network errors or failed authentication retain bundled/cache data; do not guess a login or profile
repair. Continue local stack investigation while those prerequisites remain open.
