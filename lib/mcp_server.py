"""Optional local stdio MCP interface for discovering vault entries, never values."""
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

server = MCPServer(
    "agents-vault",
    instructions="Discover credential entries and field names here. Use sec run to inject values "
    "into commands, sec set with stdin to save credentials, and sec file for attachments. "
    "Do not put credentials in tool arguments, entry names, field names, or attachment names. "
    "For login accounts, use UserName for the email/login and Password for the password.",
    log_level="WARNING",
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def catalog():
    """Read a fresh snapshot and return only explicitly selected names."""
    vault = Path(os.environ.get("SEC_VAULT_DIR", Path.home() / ".agents-vault"))
    try:
        result = subprocess.run(
            ["keepassxc-cli", "export", "-q", "--no-password", "-k",
             str(vault / "agents.keyx"), "-f", "xml", str(vault / "agents.kdbx")],
            capture_output=True, timeout=30, check=True,
        )
        root = ET.fromstring(result.stdout)
    except (OSError, subprocess.SubprocessError, ET.ParseError):
        raise ToolError("Cannot read vault. Check local installation, key, and permissions.") from None
    group = root.find("Root/Group")
    if group is None:
        raise ToolError("Vault has no root group.")
    entries = []

    def walk(group, parents):
        for entry in group.findall("Entry"):
            title = next((s.findtext("Value") or "" for s in entry.findall("String")
                          if s.findtext("Key") == "Title"), "")
            entries.append({
                "entry": "/".join([*parents, title]),
                "fields": sorted(s.findtext("Key") or "" for s in entry.findall("String")),
                "attachments": sorted(b.findtext("Key") or "" for b in entry.findall("Binary")),
            })
        for child in group.findall("Group"):
            walk(child, [*parents, child.findtext("Name") or ""])

    walk(group, [])
    return sorted(entries, key=lambda item: item["entry"])


@server.tool(annotations=READ_ONLY)
def list_entries(group: str = "") -> list[str]:
    """List entry paths, optionally within a group. Returns names only, no values."""
    prefix = group.strip("/")
    return [item["entry"] for item in catalog()
            if not prefix or item["entry"].startswith(prefix + "/")]


@server.tool(annotations=READ_ONLY)
def find_entries(query: str) -> list[str]:
    """Search entry paths, field names, and attachment names (case-insensitive), not values or notes."""
    query = query.casefold()
    return [item["entry"] for item in catalog()
            if any(query in name.casefold()
                   for name in [item["entry"], *item["fields"], *item["attachments"]])]


@server.tool(annotations=READ_ONLY)
def describe_entry(entry: str) -> dict:
    """Return an entry's field and attachment names; never their contents or metadata values."""
    matches = [item for item in catalog() if item["entry"] == entry]
    if len(matches) != 1:
        raise ToolError("Entry not found or path is ambiguous. Use list_entries to inspect paths.")
    return matches[0]


if __name__ == "__main__":
    server.run(transport="stdio")
