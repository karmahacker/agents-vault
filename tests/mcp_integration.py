#!/usr/bin/env python3
"""Optional MCP integration checks; run with the MCP venv's Python."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
import mcp_server


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_discovery_and_no_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            vault.mkdir()
            (vault / "backups").mkdir()
            env = dict(os.environ, SEC_VAULT_DIR=str(vault),
                       SEC_IMPORTER=str(REPO / "lib/import.py"), PYTHONDONTWRITEBYTECODE="1")

            def command(*args, **kwargs):
                return subprocess.run(args, env=env, capture_output=True, text=True,
                                      check=True, **kwargs)

            command("keepassxc-cli", "db-create", "-q", "--set-key-file",
                    str(vault / "agents.keyx"), str(vault / "agents.kdbx"))
            secret = "mcp-fixture-secret-value"
            command(str(REPO / "bin/sec"), "set", "app/login", "-u", "private-email@example.test",
                    "--notes", "private-notes-fixture", "--url", "https://private-url.example.test",
                    input=secret)
            attachment = root / "attachment.txt"
            attachment.write_text("private-attachment-fixture")
            command(str(REPO / "bin/sec"), "file", "put", "app/login", "account.txt", str(attachment))
            source = root / "app.env"
            source.write_text("API_TOKEN=private-custom-field-fixture\n")
            config = root / "sources.toml"
            config.write_text(f'[[env]]\nentry="app/api"\nfile="{source}"\n')
            command(str(REPO / "bin/sec"), "import", "--config", str(config))
            before = hashlib.sha256((vault / "agents.kdbx").read_bytes()).digest()
            params = StdioServerParameters(command=sys.executable,
                                            args=[str(REPO / "lib/mcp_server.py")], env=env)
            async with Client(params, read_timeout_seconds=10) as client:
                tools = await client.list_tools()
                self.assertEqual({t.name for t in tools.tools},
                                 {"list_entries", "find_entries", "describe_entry"})
                self.assertTrue(all(t.annotations.read_only_hint for t in tools.tools))
                for name, args, expected in [
                    ("list_entries", {"group": "app"}, "app/login"),
                    ("find_entries", {"query": "ACCOUNT.TXT"}, "app/login"),
                    ("find_entries", {"query": "api_token"}, "app/api"),
                    ("describe_entry", {"entry": "app/login"}, "UserName"),
                    ("describe_entry", {"entry": "app/api"}, "API_TOKEN"),
                ]:
                    result = await client.call_tool(name, args)
                    self.assertFalse(result.is_error)
                    payload = result.model_dump_json()
                    self.assertIn(expected, payload)
                    for hidden in (secret, "private-email", "private-notes", "private-url",
                                   "private-attachment", "private-custom-field"):
                        self.assertNotIn(hidden, payload)
                missing = await client.call_tool("describe_entry", {"entry": "absent"})
                self.assertTrue(missing.is_error)
                self.assertEqual(before, hashlib.sha256((vault / "agents.kdbx").read_bytes()).digest())
                # The server must see changes made by the CLI without restarting.
                command(str(REPO / "bin/sec"), "gen", "app/new")
                updated = await client.call_tool("list_entries", {})
                self.assertIn("app/new", updated.model_dump_json())
                (vault / "agents.keyx").rename(vault / "unavailable.keyx")
                failure = await client.call_tool("list_entries", {})
                self.assertTrue(failure.is_error)
                self.assertNotIn(secret, failure.model_dump_json())

    async def test_backend_errors_do_not_expose_subprocess_output(self):
        error = subprocess.CalledProcessError(1, ["keepassxc-cli"],
                                             output=b"private-output", stderr=b"private-error")
        with patch.object(mcp_server.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(mcp_server.ToolError, "Cannot read vault") as caught:
                mcp_server.catalog()
        self.assertNotIn("private-", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
