#!/usr/bin/env python3
"""agents-vault importer: copy credentials that already exist on this host
into the agents vault, as described by a TOML sources file.

Entries are rendered as KeePass XML, loaded with `keepassxc-cli import` and
merged into the vault with `keepassxc-cli merge`, so the file stays native
KeePassXC (pykeepass-written files fail in KeePassXC 2.7 with "Header doesn't
match hash"). Existing group/entry UUIDs are reused, unchanged entries are
skipped, changed ones keep their previous version in entry history, and
entries that are not listed in the sources file are never touched.

Never prints secret values, only entry and field names.

Usage: import.py [--dry-run] [--config PATH]
  config: $AGENTS_VAULT_SOURCES or ~/.config/agents-vault/sources.toml
  vault:  $SEC_VAULT_DIR or ~/.agents-vault
"""
import argparse, base64, calendar, configparser, fcntl, gzip, json, os, re
import subprocess, sys, tempfile, time, tomllib, uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from common import backup, parse_env

HOME = Path.home()
VAULT = Path(os.environ.get("SEC_VAULT_DIR", HOME / ".agents-vault"))
DB, KEY = VAULT / "agents.kdbx", VAULT / "agents.keyx"
DEFAULT_CONFIG = Path(os.environ.get("AGENTS_VAULT_SOURCES", HOME / ".config/agents-vault/sources.toml"))
NS = uuid.UUID("5b7f3c2e-6a1d-4c8e-9f20-a9e5c0d1b2f3")
STD = ("Title", "UserName", "Password", "URL", "Notes")

# Which .env variables count as secrets (stored as protected fields).
SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|_PASS$|SECRET|TOKEN|API_?KEY|_KEY$|ACCESS_KEY|AUTHORIZATION|COMMUNITY|HASHER_IV|ROLE_ID|PRIVATE)", re.I)
NOT_SECRET = re.compile(r"(EXPIRE|EXPIRATION|TTL|INTERVAL|GAP|MAX_AGE|PUBLIC|_PATH$|_FILE$|_ENVIRONMENT$|BOT_ID$|_URL$)", re.I)
CRED_URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^:/@\s]+:[^@\s]+@", re.I)


def warn(msg):
    print(f"skip: {msg}", file=sys.stderr)


def p(path):
    return Path(os.path.expanduser(path))


def kpcli(*args):
    return subprocess.run(["keepassxc-cli", *args], capture_output=True)


def b64uuid(u):
    return base64.b64encode(u.bytes).decode()


def is_secret(name, value, extra=()):
    if not value:
        return False
    if name in extra or CRED_URL.match(value):
        return True
    return bool(SECRET_NAME.search(name)) and not NOT_SECRET.search(name)


class Vault:
    """Collects entries, then writes only new/changed ones into the vault."""

    def __init__(self):
        self.entries = {}

    def _read_existing(self):
        r = kpcli("export", "--no-password", "-k", str(KEY), "-f", "xml", str(DB))
        if r.returncode:
            sys.exit(f"cannot export vault: {r.stderr.decode()[:300]}")
        root = ET.fromstring(r.stdout)
        bins = {b.get("ID"): b for b in root.iter("Binary") if b.get("ID") is not None}
        groups, entries = {}, {}

        def walk(g, path):
            for e in g.findall("Entry"):
                s = {x.findtext("Key"): x.findtext("Value") or "" for x in e.findall("String")}
                files = {}
                for b in e.findall("Binary"):
                    ref = b.find("Value").get("Ref")
                    if ref not in bins:
                        continue
                    raw = base64.b64decode(bins[ref].text or "")
                    if bins[ref].get("Compressed") == "True":
                        raw = gzip.decompress(raw)
                    files[b.findtext("Key")] = raw
                mtime = e.findtext("Times/LastModificationTime") or ""
                entries["/".join(path + [s.get("Title", "")])] = (e.findtext("UUID"), s, files, mtime)
            for sg in g.findall("Group"):
                gp = path + [sg.findtext("Name")]
                groups["/".join(gp)] = sg.findtext("UUID")
                walk(sg, gp)

        walk(root.find("Root/Group"), [])
        return groups, entries

    def put(self, path, username="", password="", url="", notes="", fields=None, files=None):
        strings = {"Title": path.rsplit("/", 1)[-1], "UserName": username or "",
                   "Password": password or "", "URL": url or "", "Notes": notes or ""}
        strings.update(fields or {})
        self.entries[path] = (strings, dict(files or {}))

    def changes(self):
        out = {}
        for path, (strings, files) in self.entries.items():
            old = self.existing.get(path)
            if not old:
                out[path] = "new"
            elif old[1] != strings or old[2] != files:
                out[path] = "updated"
        return out

    @staticmethod
    def _stamp(old_mtime):
        """Modification time for a rewritten entry. keepassxc-cli merge only takes the
        incoming entry when it is strictly newer, so stay at least 1s past the old one."""
        now = time.time()
        try:
            prev = calendar.timegm(time.strptime(old_mtime, "%Y-%m-%dT%H:%M:%SZ"))
            now = max(now, prev + 1)
        except (TypeError, ValueError):
            pass
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    def write(self, changes):
        kf = ET.Element("KeePassFile")
        meta = ET.SubElement(kf, "Meta")
        ET.SubElement(meta, "Generator").text = "agents-vault import"
        binaries = ET.SubElement(meta, "Binaries")
        root = ET.SubElement(ET.SubElement(kf, "Root"), "Group")
        ET.SubElement(root, "UUID").text = b64uuid(uuid.uuid5(NS, "<root>"))
        ET.SubElement(root, "Name").text = "Root"
        gnodes = {"": root}

        def group(path):
            if path not in gnodes:
                parent, _, name = path.rpartition("/")
                g = ET.SubElement(group(parent), "Group")
                ET.SubElement(g, "UUID").text = self.groups.get(path) or b64uuid(uuid.uuid5(NS, "group:" + path))
                ET.SubElement(g, "Name").text = name
                gnodes[path] = g
            return gnodes[path]

        nbin = 0
        for path in changes:
            strings, files = self.entries[path]
            e = ET.SubElement(group(path.rpartition("/")[0]), "Entry")
            old = self.existing.get(path)
            ET.SubElement(e, "UUID").text = old[0] if old else b64uuid(uuid.uuid5(NS, "entry:" + path))
            stamp = self._stamp(old[3] if old else None)
            times = ET.SubElement(e, "Times")
            for tag in ("CreationTime", "LastModificationTime", "LastAccessTime", "LocationChanged"):
                ET.SubElement(times, tag).text = stamp
            for k, val in strings.items():
                s = ET.SubElement(e, "String")
                ET.SubElement(s, "Key").text = k
                v = ET.SubElement(s, "Value")
                v.text = val
                if k not in STD or k == "Password":
                    v.set("ProtectInMemory", "True")
            for name, data in files.items():
                b = ET.SubElement(binaries, "Binary", ID=str(nbin), Compressed="False")
                b.text = base64.b64encode(data).decode()
                eb = ET.SubElement(e, "Binary")
                ET.SubElement(eb, "Key").text = name
                ET.SubElement(eb, "Value", Ref=str(nbin))
                nbin += 1

        with tempfile.TemporaryDirectory(dir=VAULT, prefix=".import-") as tmp:
            xml, kdbx = Path(tmp, "in.xml"), Path(tmp, "in.kdbx")
            fd = os.open(xml, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                ET.ElementTree(kf).write(fh, encoding="utf-8", xml_declaration=True)
            r = kpcli("import", "-q", "--set-key-file", str(KEY), str(xml), str(kdbx))
            if r.returncode:
                sys.exit(f"import failed: {r.stderr.decode()[:300]}")
            r = kpcli("merge", "-q", "-s", "--no-password", "-k", str(KEY), str(DB), str(kdbx))
            if r.returncode:
                sys.exit(f"merge failed: {r.stderr.decode()[:300]}")


# ---- source types -----------------------------------------------------------
# Each takes the Vault and one table from the sources file.

def src_env(v, s):
    """A dotenv file: secret vars become protected fields, whole file attached as .env."""
    f = p(s["file"])
    if not f.exists():
        return warn(f"{s['entry']}: {s['file']} missing")
    data = f.read_bytes()
    env = parse_env(data.decode(errors="replace"))
    fields = {k: val for k, val in env.items() if is_secret(k, val, s.get("secret_vars", ()))}
    notes = (f"{s.get('desc', '')}\nsource: {s['file']}\n"
             f"secret fields: {', '.join(sorted(fields)) or '-'}\n"
             f"whole file: attachment .env  (sec run @{s['entry']} -- cmd | sec file get {s['entry']} .env <out>)")
    v.put(s["entry"], notes=notes, fields=fields, files={".env": data})


def src_ssh(v, s):
    """An SSH private key (+ .pub if present) as attachments."""
    key = p(s["key"])
    if not key.exists():
        return warn(f"{s['entry']}: {s['key']} missing")
    files = {key.name: key.read_bytes()}
    pub = key.with_name(key.name + ".pub")
    if pub.exists():
        files[pub.name] = pub.read_bytes()
    user, host = s.get("user", ""), s.get("host", "")
    v.put(s["entry"], username=user, url=f"ssh://{user}@{host}" if host else "",
          notes=f"{s.get('desc', '')}\nkey file: {s['key']}  (export: sec file get {s['entry']} {key.name} <out>)",
          files=files)


def src_files(v, s):
    """Arbitrary files, or every file in a directory, as attachments."""
    files = {}
    for path in s["paths"]:
        f = p(path)
        if f.is_dir():
            files.update({c.name: c.read_bytes() for c in sorted(f.iterdir())
                          if c.is_file() and c.suffix not in s.get("skip_suffixes", [])})
        elif f.exists():
            files[f.name] = f.read_bytes()
    if not files:
        return warn(f"{s['entry']}: no files found")
    password = ""
    if "password_file" in s and p(s["password_file"]).exists():
        password = p(s["password_file"]).read_text().strip()
    v.put(s["entry"], username=s.get("username", ""), password=password, url=s.get("url", ""),
          notes=f"{s.get('desc', '')}\nsource: {', '.join(s['paths'])}", files=files)


def src_value(v, s):
    """One secret read from a file: whole content, or the first regex group."""
    f = p(s["file"])
    if not f.exists():
        return warn(f"{s['entry']}: {s['file']} missing")
    text = f.read_text()
    if "pattern" in s:
        m = re.search(s["pattern"], text, re.M)
        if not m:
            return warn(f"{s['entry']}: pattern not found in {s['file']}")
        val = m.group(1)
    else:
        val = text.strip()
    v.put(s["entry"], username=s.get("username", ""), password=val, url=s.get("url", ""),
          notes=f"{s.get('desc', '')}\nsource: {s['file']}",
          fields={s["field"]: val} if s.get("field") else None)


def src_aws_profile(v, s):
    """Static keys of one profile in ~/.aws/credentials."""
    cp = configparser.ConfigParser()
    cp.read(p(s.get("file", "~/.aws/credentials")))
    if s["profile"] not in cp:
        return warn(f"{s['entry']}: aws profile {s['profile']} missing")
    sec = cp[s["profile"]]
    kid, secret = sec.get("aws_access_key_id", ""), sec.get("aws_secret_access_key", "")
    v.put(s["entry"], username=kid, password=secret, url="https://console.aws.amazon.com",
          notes=f"{s.get('desc', '')}\nsource: ~/.aws/credentials [{s['profile']}]",
          fields={"AWS_ACCESS_KEY_ID": kid, "AWS_SECRET_ACCESS_KEY": secret})


def src_gh_token(v, s):
    """The token the gh CLI is logged in with."""
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    tok = r.stdout.strip()
    if not tok:
        return warn(f"{s['entry']}: gh not logged in")
    v.put(s["entry"], username=s.get("username", ""), password=tok, url="https://github.com",
          notes=f"{s.get('desc', '')}\nsource: gh auth token", fields={"GH_TOKEN": tok})


def src_docker_auths(v, s):
    """Every registry login in a docker config.json, one entry each under <group>/."""
    f = p(s.get("file", "~/.docker/config.json"))
    if not f.exists():
        return warn(f"docker auths: {f} missing")
    for reg, a in json.loads(f.read_text()).get("auths", {}).items():
        if a.get("auth"):
            user, _, pw = base64.b64decode(a["auth"]).decode().partition(":")
            v.put(f"{s['group']}/{reg}", username=user, password=pw, url=f"https://{reg}",
                  notes=f"docker registry login for {reg}\nsource: {f}")


def src_cloudflared_tunnels(v, s):
    """Each locally-managed tunnel: its YAML config + credentials JSON."""
    d = p(s.get("dir", "~/.cloudflared"))
    for yml in sorted(d.glob("*.yml")):
        m = re.search(r"^tunnel:\s*(\S+)", yml.read_text(), re.M)
        if not m:
            continue
        tid = m.group(1)
        files = {yml.name: yml.read_bytes()}
        creds = d / f"{tid}.json"
        if creds.exists():
            files[creds.name] = creds.read_bytes()
        v.put(f"{s['group']}/tunnel-{yml.stem}", username=tid, url="https://one.dash.cloudflare.com",
              notes=f"locally-managed cloudflared tunnel {yml.stem} ({tid}). {s.get('desc', '')}\n"
                    f"config {d}/{yml.name}; credentials {d}/{tid}.json", files=files)


def src_k8s_secret(v, s):
    """One key of a Kubernetes secret (read with kubectl)."""
    kubectl = os.path.expanduser(s.get("kubectl", "kubectl"))
    r = subprocess.run([kubectl, "--context", s["context"], "get", "secret", "-n", s["namespace"], s["secret"],
                        "-o", f"jsonpath={{.data.{s['key']}}}"], capture_output=True, text=True, timeout=30)
    if r.returncode or not r.stdout:
        return warn(f"{s['entry']}: kubectl failed ({r.stderr.strip()[:120]})")
    val = base64.b64decode(r.stdout).decode().strip()
    v.put(s["entry"], password=val, url=s.get("url", ""),
          notes=f"{s.get('desc', '')}\nsource of truth: k8s context {s['context']} secret "
                f"{s['namespace']}/{s['secret']} key {s['key']}",
          fields={s["field"]: val} if s.get("field") else None)


SOURCES = {
    "env": src_env, "ssh": src_ssh, "files": src_files, "value": src_value,
    "aws_profile": src_aws_profile, "gh_token": src_gh_token, "docker_auths": src_docker_auths,
    "cloudflared_tunnels": src_cloudflared_tunnels, "k8s_secret": src_k8s_secret,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    args = ap.parse_args()
    if not args.config.exists():
        sys.exit(f"no sources file at {args.config} (see examples/sources.example.toml)")
    cfg = tomllib.loads(args.config.read_text())
    unknown = set(cfg) - set(SOURCES)
    if unknown:
        sys.exit(f"unknown source types in {args.config}: {', '.join(sorted(unknown))}")

    v = Vault()
    for kind, items in cfg.items():
        for item in items:
            SOURCES[kind](v, item)
    with open(VAULT / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        v.groups, v.existing = v._read_existing()
        changes = v.changes()
        if changes and not args.dry_run:
            backup(VAULT)
            v.write(changes)
    for path, change in changes.items():
        strings, attachments = v.entries[path]
        fields = sorted(set(strings) - set(STD))
        files = sorted(attachments)
        bits = (["password"] if strings["Password"] else []) + ([f"{len(fields)} fields: {' '.join(fields)}"] if fields else []) \
            + ([f"files: {' '.join(files)}"] if files else [])
        print(f"{change:8} {path:40} {'; '.join(bits)}")
    verb = "would change" if args.dry_run else "new/updated"
    print(f"{len(changes)} {verb}, {len(v.entries) - len(changes)} unchanged")


if __name__ == "__main__":
    main()
