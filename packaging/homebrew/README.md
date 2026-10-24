# Homebrew packaging experiment

This branch tests a local, unpublished macOS tap. It is not a Homebrew core submission,
release, or supported distribution channel yet. Start in a checkout of
`experiment/homebrew-packaging` with Homebrew installed.

```bash
brew install --cask keepassxc
./packaging/homebrew/prepare-tap.sh
brew install --HEAD agents-vault/experimental/agents-vault
brew test agents-vault/experimental/agents-vault
```

The preparation script creates a local `agents-vault/experimental` tap, rendering the formula
with this checkout's origin URL and the experimental branch. It does not publish a repository.
The formula fetches committed code from that branch; uncommitted edits are not included.
For a different source, set `AGENTS_VAULT_SOURCE_URL` and `AGENTS_VAULT_SOURCE_BRANCH` when
running the preparation script. CI uses its local checkout as the source to test the exact commit.

## Separate installation from setup

`brew install` installs code and Python only. KeePassXC is a separate cask prerequisite.
It does not create a vault, import credentials, edit agent instructions, or start a schedule.
Check `type -a sec` if a previous `~/.local/bin/sec` or `/usr/local/bin/sec` shadows Homebrew's
command. Use `"$(brew --prefix agents-vault/experimental/agents-vault)/bin/sec"` explicitly
until you decide which installation to keep; do not delete your existing vault.

Initialize user state explicitly:

```bash
sec init --no-import
sec doctor
```

`sec init` supports `--sources FILE`, `--no-timer`, `--no-rules`, and `--no-import`.
It preserves an existing database, key, and sources file, and refuses an incomplete vault/key
pair. By default it configures agent rules and the hourly scheduler. Configure the generated
sources file before importing. `sec doctor` checks dependencies, permissions, and whether the
vault opens; it does not validate scheduler status, source availability, or client configuration.

For an isolated trial, set `SEC_VAULT_DIR` and `SEC_CONFIG_DIR` to temporary directories and
use `sec init --no-timer --no-rules --no-import`. These overrides do not change the location
of global agent instructions or scheduler files, so keep those flags for isolated tests.

Update package code without running setup:

```bash
brew upgrade --fetch-HEAD agents-vault/experimental/agents-vault
```

Run `sec init --no-import` separately when you want to refresh installed instructions or
scheduler configuration. Uninstalling the formula leaves user data in place. Disable its
LaunchAgent before uninstalling to avoid a schedule pointing to an absent command.

## Current limits and validation

- HEAD-only, macOS-only formula; no versioned release, bottles, or published tap.
- The optional MCP server is not packaged in this experiment; use the existing installer for MCP.
- The existing `./install.sh` still performs installation and setup together, using the same
  setup implementation as `sec init`.
- macOS CI installs and tests the formula, reinstalls it, checks that vault/key/config hashes
  are unchanged, then uninstalls it and checks those hashes again.
- Before a release: select a project license, add a versioned source archive and checksum,
  decide how to package optional MCP dependencies, and review migration from manual installs.

The approach follows Homebrew's [tap guide](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap)
and [formula cookbook](https://docs.brew.sh/Formula-Cookbook).
