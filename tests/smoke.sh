#!/usr/bin/env bash
# End-to-end test of sec + importer against a throwaway vault. Touches nothing real.
#   tests/smoke.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export SEC_VAULT_DIR="$T/vault" SEC_IMPORTER="$REPO/lib/import.py" AGENTS_VAULT_SOURCES="$T/sources.toml"
sec() { "$REPO/bin/sec" "$@"; }
pass=0
ok()   { pass=$((pass + 1)); echo "ok   $*"; }
fail() { echo "FAIL $*" >&2; exit 1; }
eq()   { [[ $1 == "$2" ]] && ok "$3" || fail "$3: got '$1' want '$2'"; }

mkdir -p "$SEC_VAULT_DIR/backups"
keepassxc-cli db-create -q --set-key-file "$SEC_VAULT_DIR/agents.keyx" "$SEC_VAULT_DIR/agents.kdbx"

# --- importer
cat >"$T/app.env" <<'E'
NODE_ENV=development
DB_PASSWORD="p@ss word"
JWT_SECRET=jwt123
DATABASE_URL=postgres://app:pw@db:5432/app
PUBLIC_URL=https://example.com
TOKEN_TTL=3600
E
printf 'KEY-MATERIAL\n' >"$T/id_test"; printf 'ssh-ed25519 AAAA test\n' >"$T/id_test.pub"
printf 'access-token: tok-abc\n' >"$T/doctl.yaml"
cat >"$AGENTS_VAULT_SOURCES" <<E
[[env]]
entry = "app/api-dev"
file  = "$T/app.env"
desc  = "test app"
[[ssh]]
entry = "ssh/test"
key   = "$T/id_test"
user  = "deploy"
host  = "203.0.113.1"
[[value]]
entry   = "do/doctl"
file    = "$T/doctl.yaml"
pattern = '^access-token:\\s*(\\S+)'
field   = "DIGITALOCEAN_ACCESS_TOKEN"
E
out="$(sec import)"; grep -q "3 new/updated" <<<"$out" || fail "first import: $out"; ok "import creates 3 entries"
grep -q "tok-abc\|jwt123\|p@ss" <<<"$out" && fail "import printed a secret value"; ok "import output has no values"
out="$(sec import)"; grep -q "0 new/updated, 3 unchanged" <<<"$out" || fail "re-import: $out"; ok "re-import is a no-op"

eq "$(sec get app/api-dev DB_PASSWORD)" "p@ss word" "env secret field (quoted value)"
eq "$(sec get app/api-dev DATABASE_URL)" "postgres://app:pw@db:5432/app" "credential URL detected"
sec info app/api-dev | grep -q "^NODE_ENV:" && fail "non-secret var stored as field"; ok "non-secret vars not stored"
sec info app/api-dev | grep -q "^TOKEN_TTL:" && fail "TTL var stored"; ok "TTL var not stored"
sec info app/api-dev | grep -q "p@ss" && fail "info leaked a value"; ok "info hides values"
eq "$(sec run @app/api-dev -- sh -c 'echo "$NODE_ENV|$DB_PASSWORD"')" "development|p@ss word" "run @entry loads whole .env"
eq "$(sec run X=do/doctl:DIGITALOCEAN_ACCESS_TOKEN -- sh -c 'echo $X')" "tok-abc" "run VAR=entry:field"
sec file get ssh/test id_test "$T/k" >/dev/null; cmp -s "$T/k" "$T/id_test" && ok "ssh key round-trips" || fail "ssh key differs"
eq "$(stat -c %a "$T/k")" "600" "exported file is 0600"

printf 'JWT_SECRET=changed\n' >>"$T/app.env"
out="$(sec import)"; grep -q "updated  app/api-dev" <<<"$out" || fail "changed source not updated: $out"; ok "changed source updates entry"
eq "$(sec get app/api-dev JWT_SECRET)" "changed" "updated value visible"

# --- hand-managed entries
printf 'hunter2' | sec set manual/thing -u me --url https://x.test --notes "hand-added" >/dev/null
eq "$(sec get manual/thing)" "hunter2" "set stores secret from stdin"
printf 'hunter3\n' | sec set manual/thing >/dev/null
eq "$(sec get manual/thing)" "hunter3" "set updates existing"
eq "$(sec get manual/thing UserName)" "me" "update keeps username"
sec meta manual/thing --notes "new notes" >/dev/null
eq "$(sec get manual/thing Notes)" "new notes" "meta edits notes"
eq "$(sec get manual/thing)" "hunter3" "meta keeps secret"
sec gen manual/gen -L 24 >/dev/null
eq "$(sec get manual/gen | tr -d '\n' | wc -c)" "24" "gen creates password of length"
sec set manual/empty </dev/null 2>/dev/null && fail "empty secret accepted"; ok "empty secret rejected"
sec import >/dev/null
eq "$(sec get manual/thing)" "hunter3" "import leaves hand-added entries alone"
printf 'cert\n' >"$T/c.pem"; sec file put manual/cert tls.pem "$T/c.pem" >/dev/null
sec file get manual/cert tls.pem "$T/c2.pem" >/dev/null; cmp -s "$T/c.pem" "$T/c2.pem" && ok "file put/get" || fail "file put/get"
sec rm manual/gen >/dev/null; sec ls | grep -q "^manual/gen$" && fail "rm"; ok "rm removes entry"
[[ $(sec backups | wc -l) -gt 0 ]] && ok "writes leave backups" || fail "no backups"
keepassxc-cli ls -q --no-password -k "$SEC_VAULT_DIR/agents.keyx" "$SEC_VAULT_DIR/agents.kdbx" >/dev/null && ok "vault still opens in keepassxc-cli"

echo "all $pass checks passed"
