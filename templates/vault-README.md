# Agents vault

KeePassXC database `~/.agents-vault/agents.kdbx`, unlocked by key file `agents.keyx` (no password).
Every agent on this host uses it through `sec` (run `sec help`). Installed from the agents-vault repo.

## Rules
1. Before asking the user for ANY password, token, API key, SSH key, certificate or .env file: `sec find <term>` / `sec ls`.
2. Use secrets without printing them: `sec run VAR=entry[:field] ... -- cmd` or `sec run @entry -- cmd` (whole .env).
3. `sec get` only to write a value into a file/config that needs it. Never echo secrets into chat, logs, commits, PR text or command-line arguments.
4. New credentials you create or receive: `printf %s "$VAL" | sec set <service>/<name> -u <user> --url <url> --notes "<what it is, where it is used>"`. Secret only via stdin.
5. No prod credentials in this vault, ever.

## Finding things
    sec ls                       # every entry path
    sec find <term>              # matches titles, notes (notes list each entry's field names)
    sec info <entry>             # username, url, notes, field names, attachments; values hidden

Conventions: one entry per service env (`<project>/<service>-<env>`) whose secret vars are fields and whose
whole .env file is the attachment `.env`; `ssh/<host>` entries hold private keys as attachments with
URL `ssh://user@host`; API tokens keep the env-var name they are used under as a field
(e.g. `CLOUDFLARE_API_TOKEN`, `GH_TOKEN`).

## Using things
    sec run @myapp/api-dev -- npm run start:dev               # whole .env into the env
    sec run TOKEN=cloudflare/dns-edit:CLOUDFLARE_API_TOKEN -- ./dns.sh
    sec file get myapp/api-dev .env ./new-worktree/.env      # recreate an .env file (mode 0600)
    sec file get ssh/build-server build_server /tmp/k        # then ssh -i /tmp/k ...; delete after

## Adding things
    printf %s "$TOKEN" | sec set openai/dev-key --url https://platform.openai.com --notes "used by X"
    sec gen myapp/new-db-user -u app -L 32                   # generate + store a password
    sec file put certs/myapp-tls tls.key ./tls.key

## Maintenance
- Entries listed in `~/.config/agents-vault/sources.toml` are imported from files on this host by
  `sec import` (hourly systemd timer on Linux or `local.agents-vault.import` LaunchAgent on macOS). Editing a source file updates its entry;
  never edit those entries by hand. Everything else is hand/agent-managed and never overwritten.
- Every write is backed up first (`sec backups`, last 200 kept in ~/.agents-vault/backups).
- Open in the KeePassXC GUI with key file agents.keyx and an empty password.

## Updating the installed tools
From the repository checkout, run `git switch main`, `git pull --ff-only`, then `./install.sh`.
Use `dev` instead of `main` if that branch exists and is the version you want to install.
Use `./install.sh --no-import` to update software without refreshing imported entries; reuse any
original `--no-timer`, `--no-rules`, or `--no-system-link` flags. The installer retains the vault,
key and sources configuration, refreshes this guide, and saves a differing previous guide as
`README.md.previous.*`. Pulling the repository alone does not update the installed tools.

## Optional MCP discovery
Run `./install.sh --with-mcp --no-import` from the checkout to install the optional MCP SDK in
its own venv. Configure your client's local stdio server command as the absolute path to
`~/.local/bin/sec-mcp`, running as this user on this host. Installation does not configure clients.
The read-only tools `list_entries`, `find_entries`, and `describe_entry` return paths, field names,
and attachment names only. Credential values, usernames, URLs, notes, and file contents are omitted.
MCP makes tools easier to discover but adds dependencies and client setup; names can enter model
context. It adds no per-agent permissions. Continue using `sec run`, `sec set`, and `sec file`
for credential operations. Never pass secrets in MCP arguments or names. The CLI works without MCP.

For email/password accounts, use `sec set <entry> -u <email>` with the password on stdin.
Use `sec run LOGIN=entry:UserName PASSWORD=entry:Password -- cmd` to supply both without printing.

## macOS notes
The installer uses Homebrew Python and KeePassXC (or the CLI inside the installed KeePassXC app).
Commands live in `~/.local/bin`; use absolute paths if your agent does not include it in PATH.
Hourly imports use `~/Library/LaunchAgents/local.agents-vault.import.plist`, loaded at graphical
login. Check with `launchctl print "gui/$(id -u)/local.agents-vault.import"`.
Imports pause during logout and sleep; use `sec import` for an immediate refresh.
Logs are in the vault directory as `import.log` and `import-error.log` and are not rotated.
Linux uses systemd with optional linger instead. `--no-timer` skips setup on either OS.
