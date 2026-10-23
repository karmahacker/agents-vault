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
  `sec import` (hourly user timer `agents-vault-import.timer`). Editing a source file updates its entry;
  never edit those entries by hand. Everything else is hand/agent-managed and never overwritten.
- Every write is backed up first (`sec backups`, last 200 kept in ~/.agents-vault/backups).
- Open in the KeePassXC GUI with key file agents.keyx and an empty password.
