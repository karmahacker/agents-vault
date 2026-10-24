#!/usr/bin/env bash
# Fail if a file tracked or staged in this repo contains
#   1. any value stored in the local agents vault (passwords/fields >= 8 chars,
#      lines >= 20 chars of non-.env attachments such as keys and certs);
#   2. something that identifies a real environment: an IPv4 address outside
#      loopback/private/documentation ranges, a 12-digit number (AWS account id),
#      a 32-hex id or a UUID (unless listed in tools/allowlist.txt);
#   3. any term from the private denylist ~/.config/agents-vault/denylist.txt
#      (your domains, hostnames, user/project/account names; never committed).
# Also usable as a pre-commit hook:  ln -s ../../tools/check-no-secrets.sh .git/hooks/pre-commit
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
VAULT_DIR="${SEC_VAULT_DIR:-$HOME/.agents-vault}"
DENYLIST="${AGENTS_VAULT_DENYLIST:-$HOME/.config/agents-vault/denylist.txt}"

vault_xml=""
if [[ -r $VAULT_DIR/agents.kdbx ]]; then
  vault_xml="$(mktemp)"; trap 'rm -f "$vault_xml"' EXIT
  keepassxc-cli export -q --no-password -k "$VAULT_DIR/agents.keyx" -f xml "$VAULT_DIR/agents.kdbx" >"$vault_xml"
else
  echo "check-no-secrets: no vault at $VAULT_DIR, skipping vault values" >&2
fi

python3 - "$vault_xml" "$DENYLIST" tools/allowlist.txt <<'PY'
import base64, gzip, ipaddress, os, re, subprocess, sys, xml.etree.ElementTree as ET

vault_xml, denylist, allowlist = sys.argv[1:4]
needles = set()
if vault_xml:
    root = ET.parse(vault_xml).getroot()
    for s in root.iter("String"):
        if s.findtext("Key") in ("Title", "URL", "Notes", "UserName"):
            continue
        v = (s.findtext("Value") or "").strip()
        if len(v) >= 8:
            needles.add(v)
    # .env attachments: their secret values are already fields; other lines are not secret.
    # sources.toml backups are configuration (paths/names), covered by the denylist instead.
    env_refs = {b.find("Value").get("Ref") for b in root.iter("Binary")
                if b.get("ID") is None and b.findtext("Key") in (".env", "sources.toml")}
    for b in root.iter("Binary"):
        if b.get("ID") is None or b.get("ID") in env_refs:
            continue
        raw = base64.b64decode(b.text or "")
        if b.get("Compressed") == "True":
            raw = gzip.decompress(raw)
        for line in raw.decode(errors="ignore").splitlines():
            line = line.strip()
            if len(line) >= 20 and not line.startswith(("#", "-----")):
                needles.add(line)

def terms(path):
    if not os.path.exists(path):
        return []
    return [l.strip() for l in open(path) if l.strip() and not l.lstrip().startswith("#")]

deny = [t.lower() for t in terms(denylist)]
allow = set(terms(allowlist))
if not deny:
    print(f"check-no-secrets: warning: no denylist at {denylist}", file=sys.stderr)

DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
def public_ip(s):
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_reserved
                or any(ip in n for n in DOC_NETS))

ID_PATTERNS = [
    ("UUID", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("32-hex id", re.compile(r"\b[0-9a-fA-F]{32}\b")),
    ("12-digit id", re.compile(r"(?<![\d.])\d{12}(?![\d.])")),
]
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

def git_files(*args):
    return [os.fsdecode(f) for f in subprocess.run(
        ["git", "ls-files", "-z", *args], capture_output=True, check=True
    ).stdout.split(b"\0") if f]


def contents():
    # Inspect index blobs even when the working copy was edited or removed after staging.
    index = subprocess.run(["git", "ls-files", "--stage", "-z"],
                           capture_output=True, check=True).stdout
    for record in index.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        f = os.fsdecode(name)
        if f == "tools/allowlist.txt" or mode == b"160000":
            continue
        blob = subprocess.run(["git", "cat-file", "blob", oid.decode()],
                              capture_output=True, check=True).stdout
        yield f + " (staged)", blob.decode(errors="ignore")
    for f in git_files("--cached", "--others", "--exclude-standard"):
        if f == "tools/allowlist.txt":
            continue
        try:
            with open(f, errors="ignore") as source:
                yield f, source.read()
        except (IsADirectoryError, FileNotFoundError):
            continue


problems = []
checked = 0
for f, text in contents():
    checked += 1
    if any(n in text for n in needles):
        problems.append(f"{f}: contains a value stored in the vault")
    low = text.lower()
    for t in deny:
        if t in low:
            problems.append(f"{f}: contains denylisted term #{deny.index(t) + 1}")
    for m in IPV4.finditer(text):
        if public_ip(m.group()) and m.group() not in allow:
            problems.append(f"{f}: public IPv4 address {m.group()}")
    for name, rx in ID_PATTERNS:
        for m in rx.finditer(text):
            if m.group() not in allow:
                problems.append(f"{f}: {name} {m.group()[:6]}...")

for p in problems:
    print("BLOCKED", p)
print(f"check-no-secrets: {checked} file versions; {len(needles)} vault values, {len(deny)} denylist terms checked")
sys.exit(1 if problems else 0)
PY
