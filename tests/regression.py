#!/usr/bin/env python3
"""Regression checks with temporary repositories and vaults; no real credentials."""
import contextlib
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from common import backup

spec = importlib.util.spec_from_file_location("importer", REPO / "lib/import.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "backups").mkdir()
        self.config = self.root / "sources.toml"
        self.env = dict(os.environ, SEC_VAULT_DIR=str(self.vault),
                        SEC_IMPORTER=str(REPO / "lib/import.py"),
                        AGENTS_VAULT_SOURCES=str(self.config), PYTHONDONTWRITEBYTECODE="1")

    def command(self, *args, **kwargs):
        return subprocess.run(args, env=self.env, capture_output=True, text=True,
                              check=True, **kwargs).stdout

    def sec(self, *args):
        return self.command(str(REPO / "bin/sec"), *args)

    def create_vault(self):
        self.command("keepassxc-cli", "db-create", "-q", "--set-key-file",
                     str(self.vault / "agents.keyx"), str(self.vault / "agents.kdbx"))

    def test_staged_content_is_checked_after_edit_or_removal(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.command("git", "init", "-q", str(repo))
        (repo / "tools").mkdir()
        (repo / "tools/allowlist.txt").write_text("")
        deny = self.root / "denylist.txt"
        deny.write_text("synthetic-forbidden-marker\n")
        self.env["AGENTS_VAULT_DENYLIST"] = str(deny)
        path = repo / "sample.txt"
        path.write_text("synthetic-forbidden-marker\n")
        self.command("git", "add", "sample.txt", cwd=repo)
        for replacement in ("clean\n", None):
            if replacement is None:
                path.unlink()
            else:
                path.write_text(replacement)
            result = subprocess.run([str(REPO / "tools/check-no-secrets.sh")],
                                    cwd=repo, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("sample.txt (staged): contains denylisted term", result.stdout)
            self.assertNotIn("synthetic-forbidden-marker", result.stdout)
        self.command("git", "rm", "--cached", "sample.txt", cwd=repo)
        self.command(str(REPO / "tools/check-no-secrets.sh"), cwd=repo)

    def test_env_import_and_execution_agree(self):
        self.create_vault()
        source = self.root / "app.env"
        source.write_bytes(b"DB_PASSWORD=old\r\nexport DB_PASSWORD=  ' spaced value '  \r\n"
                           b"API_TOKEN=  literal$NAME#hash  \r\nEMPTY=\r\n")
        self.config.write_text(f'[[env]]\nentry="test/app"\nfile="{source}"\n')
        self.sec("import")
        for field, expected in (("DB_PASSWORD", " spaced value "),
                                ("API_TOKEN", "literal$NAME#hash")):
            self.assertEqual(self.sec("get", "test/app", field), expected + "\n")
            result = self.sec("run", "@test/app", "--", sys.executable, "-c",
                              f"import os; print(os.environ[{field!r}])")
            self.assertEqual(result, expected + "\n")
        self.assertEqual(self.sec("run", "@test/app", "EMPTY=test/app:API_TOKEN", "--",
                                  sys.executable, "-c", "import os; print(os.environ['EMPTY'])"),
                         "literal$NAME#hash\n")
        self.assertEqual(self.sec("run", "@test/app", "--", sys.executable, "-c",
                                  "import os; print(repr(os.environ['EMPTY']))"), "''\n")

    def test_both_write_paths_enforce_retention(self):
        self.create_vault()
        for i in range(205):
            (self.vault / "backups" / f"agents-20000101-000000-{i:03}.kdbx").write_text("old")
        (self.vault / "backups" / "keep.txt").write_text("unrelated")
        source = self.root / "value.txt"
        source.write_text("synthetic-value")
        self.config.write_text(f'[[value]]\nentry="test/value"\nfile="{source}"\n')
        self.sec("import")
        self.assertEqual(len(list((self.vault / "backups").glob("agents-*.kdbx"))), 200)
        self.sec("gen", "manual/value")
        self.assertEqual(len(list((self.vault / "backups").glob("agents-*.kdbx"))), 200)
        self.assertTrue((self.vault / "backups/keep.txt").exists())

    def test_backup_names_do_not_collide_within_a_second(self):
        (self.vault / "agents.kdbx").write_text("first")
        backup(self.vault)
        (self.vault / "agents.kdbx").write_text("second")
        backup(self.vault)
        self.assertEqual({p.read_text() for p in (self.vault / "backups").glob("*.kdbx")},
                         {"first", "second"})

    def test_sources_collected_before_lock_and_snapshot_under_lock(self):
        self.config.write_text("[[probe]]\n")
        events = []

        def source(vault, table):
            with open(self.vault / ".lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append("source")

        def snapshot(vault):
            with open(self.vault / ".lock", "w") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append("snapshot")
            return {}, {}

        with patch.object(importer, "VAULT", self.vault), \
             patch.object(importer, "SOURCES", {"probe": source}), \
             patch.object(importer.Vault, "_read_existing", snapshot), \
             patch.object(sys, "argv", ["import.py", "--config", str(self.config)]), \
             contextlib.redirect_stdout(io.StringIO()):
            importer.main()
        self.assertEqual(events, ["source", "snapshot"])


if __name__ == "__main__":
    unittest.main()
