import json
import re
from pathlib import Path

import pytest

from slack_personal_mcp import __version__
from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).parent.parent
README = (ROOT / "README.md").read_text()
MANIFEST_SCOPES = set(re.findall(r"^\s+- ([a-z.]+:[a-z.]+)$", (ROOT / "slack-app-manifest.yaml").read_text(), re.M))
TOOL_ROWS = re.findall(r"^\| `(slack_\w+)\((.*?)\)` \| ([RWD]) \|.*\| (.*) \|$", README, re.M)


async def test_readme_lists_every_tool_with_its_arguments_and_kind():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert sorted(name for name, *_ in TOOL_ROWS) == sorted(tools)
    for name, args, kind, _ in TOOL_ROWS:
        schema = tools[name].inputSchema
        required = set(schema.get("required", []))
        expected = [arg if arg in required else f"{arg}?" for arg in schema.get("properties", {})]
        assert (args.split(", ") if args else []) == expected, name
        hints = tools[name].annotations
        assert kind == ("R" if hints.readOnlyHint else "D" if hints.destructiveHint else "W"), name


def test_manifest_has_every_scope_the_readme_names():
    named = {scope for *_, scopes in TOOL_ROWS for scope in re.findall(r"`([a-z][a-z.]*:[a-z.]+)`", scopes)}
    assert named <= MANIFEST_SCOPES
    assert re.search(r"^  token_rotation_enabled: false$", (ROOT / "slack-app-manifest.yaml").read_text(), re.M)


def test_registry_metadata_matches_the_package():
    server = json.loads((ROOT / "server.json").read_text())
    pyproject = re.search(r'^version = "(.+)"$', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert server["version"] == server["packages"][0]["version"] == pyproject == __version__
    assert server["packages"][0]["identifier"] == "slack-personal-mcp"
    assert README.startswith(f"# slack-personal-mcp\n\n<!-- mcp-name: {server['name']} -->\n")
    assert len(server["description"]) <= 100
