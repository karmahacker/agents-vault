## Secrets (agents vault)
Credentials for the services we work with (service .env files, cloud/API tokens, SSH keys, certificates, tunnel credentials, lab devices) are in the local KeePassXC vault on this host, accessed with `sec` (`sec help`; full guide: `~/.agents-vault/README.md`).
- Check the vault BEFORE asking the user for any password, token, key, certificate or .env file: `sec find <term>`, `sec ls`, `sec info <entry>` (field names only).
- Use secrets without printing them: `sec run VAR=entry[:field] -- cmd`, or `sec run @entry -- cmd` to load a whole .env. Files/keys: `sec file get <entry> <name> <out>`.
- `sec get` only to write a value into a file that needs it. Never echo secrets into chat, logs, commits, PR text or command arguments.
- Save every new credential you create or receive: `printf %s "$VAL" | sec set <service>/<name> -u <user> --url <url> --notes "<what/where used>"` (secret via stdin only).
- Never store prod credentials here.
- If the optional agents-vault MCP server is configured, use `list_entries`, `find_entries`, and `describe_entry` for names only. Use `sec` for credential use and writes; never put credential values in MCP arguments or names.
- For login accounts, store email/login in `UserName` and password in `Password`; use `sec run LOGIN=entry:UserName PASSWORD=entry:Password -- cmd`.
