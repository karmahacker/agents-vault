# agents-vault

A local, on-prem credential store for AI coding agents (Claude Code, Codex, Gemini/Antigravity, Kimi, ...)
on an agent server. Agents look up and use passwords, tokens, SSH keys, certificates and whole `.env` files
themselves instead of asking a human, and save new credentials they create.

- **Storage:** one KeePassXC database (`~/.agents-vault/agents.kdbx`) unlocked by a key file, no password.
  Plain KDBX 4, so it opens in the KeePassXC app and can be merged with a personal KeePass database.
- **Access:** `sec`, a small bash wrapper around `keepassxc-cli`, built so secrets reach commands through
  the environment without being printed.
- **Population:** `sec import` copies credentials that already exist on the host (dotenv files, SSH keys,
  cloud CLI logins, kubeconfig, Kubernetes secrets, cloudflared tunnels, ...) into the vault, driven by a
  TOML file that lists paths only. An hourly systemd timer keeps it in step with the source files.
- **Agent instructions:** the installer adds a "Secrets (agents vault)" section to each agent's global
  instruction file, so every agent knows the vault exists and how to use it.

**This repo contains no credentials, and must never contain any.** See [Repo hygiene](#repo-hygiene).

## Contents

| path | what |
|---|---|
| `install.sh` | installs/upgrades everything on a host (idempotent, never replaces an existing vault) |
| `bin/sec` | the CLI agents use |
| `lib/import.py` | the importer behind `sec import` (stdlib Python >= 3.11 + keepassxc-cli) |
| `examples/sources.example.toml` | every source type, documented |
| `templates/agent-rule.md` | the section added to CLAUDE.md / AGENTS.md / GEMINI.md |
| `templates/vault-README.md` | guide placed at `~/.agents-vault/README.md` for agents |
| `systemd/` | hourly `agents-vault-import` user timer |
| `tests/smoke.sh` | end-to-end test on a throwaway vault |
| `tools/check-no-secrets.sh` | fails if any vault value appears in this repo (pre-commit hook) |

## Design and its trade-offs

- **On-prem, file-based, no server.** Nothing leaves the host and there is no service to crash, unseal or
  keep alive. Labs lose power and VMs; when the store is a file, agents keep working as long as the disk does.
  A vault server (Vault/OpenBao/Vaultwarden) or a cloud manager was rejected for this reason.
- **Key file, no password.** That is what lets agents open it unattended. Consequence: anyone who can read
  `agents.keyx` can read every secret in the vault. It is encrypted at rest, and access is all-or-nothing.
  That is acceptable only because the vault holds **dev/lab credentials, never prod**. Keep prod out.
- **No per-agent permissions and no read audit log.** If you need those, put a hosted secrets manager
  behind the same `sec` interface.
- **Secrets stay out of transcripts.** Agent conversations are logged and sent to model providers, so the
  rules tell agents to use `sec run` (values go into the child process's environment) rather than printing them.
- **The vault is an index, not the only copy.** The importer copies; it never moves or deletes source files.

## Install on a new agent server

Requirements: Linux with systemd, `sudo`, Python >= 3.11, `keepassxc-cli` >= 2.7 (installed by the script on
apt systems). Tested on Ubuntu 24.04 with KeePassXC 2.7.6.

```bash
git clone <this repo> agents-vault
cd agents-vault

# 1. Describe what to import (paths only, never values). Start from the example:
mkdir -p ~/.config/agents-vault
cp examples/sources.example.toml ~/.config/agents-vault/sources.toml
$EDITOR ~/.config/agents-vault/sources.toml     # rebuilding a host? restore its copy kept in the vault

# 2. Install: deps, sec, importer, vault + key file, hourly timer, agent rules, first import.
./install.sh

# 3. Let the timer run while nobody is logged in.
sudo loginctl enable-linger "$USER"

# 4. Verify.
tests/smoke.sh                 # throwaway vault, touches nothing real
sec ls                         # what got imported
sec import --dry-run           # should say "0 would change"
```

Then check that a fresh agent uses it without help, e.g.
`claude -p "Find our Cloudflare DNS token and check it works with a read-only API call; don't show it."`.

Installer flags: `--sources FILE` (install FILE as sources.toml if none exists), `--no-system-link`,
`--no-timer`, `--no-rules`, `--no-import`. Re-running `./install.sh` after `git pull` upgrades `sec` and the
importer in place and refreshes the agent rules. It never touches an existing vault, key file or sources file.

### Finding what to import on a host

List credentials without printing values, then turn the results into `[[...]]` blocks in sources.toml:

```bash
# dotenv files (skip node_modules/.git), names only
find ~ -xdev \( -name node_modules -o -name .git -o -name .cache \) -prune -o \
  -type f \( -name '.env' -o -name '.env.*' -o -name '*.env' \) -print | grep -vE '\.(example|sample|template)$'
for f in <file>; do grep -oE '^\s*(export\s+)?[A-Za-z_][A-Za-z0-9_]*=' "$f"; done   # variable names only
ls ~/.ssh ~/.aws ~/.kube ~/.docker ~/.cloudflared ~/.config/{gh,doctl,rclone} 2>/dev/null
kubectl get secrets -A | grep -iE 'token|cloudflare|api'        # cluster-held tokens
```

Skip prod/stage env files, git worktree copies of the same `.env`, and agent-tool logins (their auth files
belong to the tools themselves).

## Using it (for agents and humans)

```text
sec ls [group]                       list entry paths (no values)
sec find <term>                      search titles/notes; notes list each entry's field names
sec info <entry>                     metadata, field names, attachments; values hidden
sec get <entry> [field]              print one value (default Password)
sec run VAR=entry[:field] ... -- cmd run cmd with secrets in its environment
sec run @entry ... -- cmd            same, loading every var of the entry's .env attachment
sec set <entry> [-u user] [--url u] [--notes t]   create/update; secret read from STDIN only
sec meta <entry> [-u user] [--url u] [--notes t]  change metadata, keep the secret
sec gen <entry> [-u user] [--url u] [-L len]      create/update with a generated password
sec mv <entry> <group> | sec rm <entry>
sec file ls|put|get ...              attachments (keys, certs, kubeconfigs, .env files); exports are 0600
sec backups                          automatic pre-write backups (last 200)
sec import [--dry-run]               refresh imported entries from sources.toml
```

Examples:

```bash
sec run @myapp/api-dev -- npm run start:dev
sec run CLOUDFLARE_API_TOKEN=cloudflare/dns-edit -- ./dns.sh
sec file get myapp/api-dev .env ~/Dev/new-worktree/.env
printf %s "$NEW_TOKEN" | sec set openai/dev-key --url https://platform.openai.com --notes "used by X"
```

Entry conventions: `<project>/<service>-<env>` for env bundles (secret vars as fields plus the whole file as
attachment `.env`); `ssh/<host>` for keys (URL `ssh://user@host`); API tokens also carry the env-var name
they are used under as a field (`CLOUDFLARE_API_TOKEN`, `GH_TOKEN`, `AWS_ACCESS_KEY_ID`, ...).

## The importer

`sec import` reads `~/.config/agents-vault/sources.toml` (override with `AGENTS_VAULT_SOURCES` or `--config`).
Source types, all documented in `examples/sources.example.toml`:

| type | imports |
|---|---|
| `env` | a dotenv file. Secret-looking vars become protected fields, the whole file is attachment `.env`. Secret = name matches PASSWORD/SECRET/TOKEN/`*_KEY`/... (not TTL/INTERVAL/PUBLIC/`*_URL`...), or the value is a URL with `user:pass@`. Force more with `secret_vars = [...]` |
| `ssh` | private key + `.pub` as attachments, user/host as URL |
| `files` | files or whole directories as attachments; optional `password_file` |
| `value` | one value from a file, whole content or first `pattern` regex group; optional extra `field` |
| `aws_profile` | static keys of a profile in `~/.aws/credentials` |
| `gh_token` | the token `gh` is logged in with |
| `docker_auths` | each registry login in `~/.docker/config.json` |
| `cloudflared_tunnels` | each locally-managed tunnel's YAML + credentials JSON |
| `k8s_secret` | one key of a Kubernetes secret via `kubectl` |

How it behaves:
- Prints entry and field names only, never values.
- Builds KeePass XML, loads it with `keepassxc-cli import`, then `keepassxc-cli merge`s it into the vault.
  Existing UUIDs are reused, so entries update in place and the previous version goes to entry history.
- Skips unchanged entries, so the hourly run is a no-op unless a source changed.
- Never touches entries not listed in sources.toml, so hand-added and agent-added entries are safe. The
  reverse also holds: **don't hand-edit imported entries**, because the next import overwrites them. Change the source file.
- Takes the same lock as `sec` writes, and backs the vault up before writing.
- Adding a source type is one `src_<type>(vault, table)` function plus a `SOURCES` entry in `lib/import.py`.

## Operations

- **Backups.** Every `sec` write and every import that changes something first copies the vault to
  `~/.agents-vault/backups/`. Restore by copying a backup over `agents.kdbx`. All of these sit on the
  same disk as the vault, so also keep an off-host copy of `agents.kdbx` **and** `agents.keyx` (e.g.
  `scp` to a workstation). Without the key file the vault cannot be opened.
- **Opening in the KeePassXC app.** File > Open `agents.kdbx`, leave the password empty, choose
  `agents.keyx` as the key file.
- **Merging with a personal KeePass database.** Use KeePassXC Database > Merge from database, or
  `keepassxc-cli merge`. Both files stay plain KDBX.
- **Moving to another server.** Copy `agents.kdbx` and `agents.keyx` into `~/.agents-vault/` on the new
  host *before* running `./install.sh`. The installer keeps an existing vault.
- **Rotating the key file.** Run `keepassxc-cli db-edit --set-key-file new.keyx --no-password -k agents.keyx agents.kdbx`,
  then replace `agents.keyx` and update the off-host copy.
- **Uninstall.** `systemctl --user disable --now agents-vault-import.timer`; remove `~/.local/bin/sec`,
  `/usr/local/bin/sec`, `~/.local/share/agents-vault`, and the "Secrets (agents vault)" sections. The vault
  stays in `~/.agents-vault` until you delete it.

## Gotchas found while building it

- **Don't write the vault with pykeepass.** A pykeepass 4.x save of a KeePassXC-created KDBX 4 file left
  it unreadable by KeePassXC 2.7.6 ("Header doesn't match hash", plus one "found unused key" line per
  attachment). That is why the importer goes through XML + `keepassxc-cli import/merge`.
- **keepassxc-cli 2.7 flag names.** `show --show-attachments`, not `--attachments`. `show -a <field>` reads
  custom fields too. `add`/`edit` can't set custom fields, which is why `sec set` only manages the standard
  fields and the importer uses XML.
- **PATH in non-login shells.** Agents often run non-login shells whose PATH lacks `~/.local/bin`, so the
  installer symlinks `/usr/local/bin/sec`. The same applies to tools sources rely on: give `k8s_secret`
  an explicit `kubectl = "~/.local/bin/kubectl"` when needed.
- **`gh` prefers `GITHUB_TOKEN`/`GH_TOKEN` from the environment** over its own login. Keep that in mind
  when testing tokens with `sec run`.
- **Prisma-style DB URLs** (`?schema=public`) are rejected by `psql`. That's a client problem, not a bad
  credential.

## Repo hygiene

- `.gitignore` blocks `*.kdbx`, `*.keyx`, `*.pem`, `*.key`, `.env*`, `sources.toml` (except `examples/`).
- **Nothing from a real environment goes in this repo**: no credentials, no real sources.toml, hostnames,
  IP addresses, account/zone/tunnel ids, user or project names. Examples use `example.com`, `myapp` and the
  documentation IP ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).
- A host's real sources.toml stays on that host, with a backup copy inside its own vault:
  `sec file put agents-vault/sources-$(hostname) sources.toml ~/.config/agents-vault/sources.toml`.
- Sources files contain paths and names only; a literal password has no supported syntax, on purpose.
  Credentials with no file on the host go in by hand with `sec set`.
- Before every commit, run `tools/check-no-secrets.sh`, or install it once as a hook:
  `ln -s ../../tools/check-no-secrets.sh .git/hooks/pre-commit`. It fails if a tracked or staged file contains
  - any value stored in the local vault (passwords, fields, key/cert lines);
  - an IPv4 address outside loopback, private and documentation ranges, a 12-digit number (AWS account id),
    a 32-hex id or a UUID, unless listed in `tools/allowlist.txt`;
  - any term from your private denylist `~/.config/agents-vault/denylist.txt` (one per line: your domains,
    hostnames, user, project and account names). That file is never committed.
