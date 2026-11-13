"""Finding MCP servers to add: the registry's entries as configs to fill in, the well-known ones,
and the catalog for Settings → MCP servers"""

import httpx
import pytest

from labb.server import mcp_registry

HAVE = {"npx": True, "uvx": True, "docker": False}


def test_an_npm_package_runs_with_npx_and_asks_for_what_it_needs():
    option = mcp_registry.package_option({
        "registryType": "npm", "identifier": "@acme/files", "version": "1.2.0", "transport": {"type": "stdio"},
        "runtimeArguments": [{"type": "positional", "value": "-y"}],
        "packageArguments": [{"type": "positional", "valueHint": "folder", "isRequired": True, "description": "Where"},
                             {"type": "named", "name": "--verbose"},
                             {"type": "named", "name": "--mode", "default": "read"}],
        "environmentVariables": [{"name": "ACME_TOKEN", "isRequired": True, "isSecret": True, "description": "Token"},
                                 {"name": "ACME_REGION", "default": "eu"}],
    }, HAVE)
    # -y once; the optional flag with nothing to say is left out
    assert option["config"] == {"command": "npx", "args": ["-y", "@acme/files@1.2.0", "{{folder}}", "--mode", "{{mode}}"],
                                "env": {"ACME_TOKEN": "{{ACME_TOKEN}}", "ACME_REGION": "{{ACME_REGION}}"}}
    fields = {f["key"]: f for f in option["fields"]}
    assert fields["folder"]["required"] and fields["folder"]["label"] == "Folder"
    assert fields["ACME_TOKEN"]["secret"] and fields["ACME_TOKEN"]["label"] == "ACME_TOKEN"
    assert fields["ACME_REGION"]["default"] == "eu" and fields["mode"]["default"] == "read"
    assert option["available"] and option["runtime"] == "npx"


def test_pypi_and_images_and_what_labb_cant_run():
    pypi = mcp_registry.package_option({"registryType": "pypi", "identifier": "mcp-server-time", "version": "0.6"}, HAVE)
    assert pypi["config"] == {"command": "uvx", "args": ["mcp-server-time==0.6"]}
    image = mcp_registry.package_option({"registryType": "oci", "identifier": "ghcr.io/acme/mcp", "version": "2",
                                         "environmentVariables": [{"name": "KEY"}]}, HAVE)
    assert image["config"]["args"] == ["run", "-i", "--rm", "-e", "KEY", "ghcr.io/acme/mcp:2"]
    assert not image["available"]
    assert mcp_registry.package_option({"registryType": "nuget", "identifier": "x"}, HAVE) is None
    assert mcp_registry.package_option({"registryType": "npm", "identifier": "x", "transport": {"type": "streamable-http"}}, HAVE) is None


def test_a_remote_asks_for_the_parts_of_its_headers():
    option = mcp_registry.remote_option({"type": "sse", "url": "https://{tenant}.example.com/mcp",
                                         "variables": {"tenant": {"isRequired": True, "description": "Your tenant"}},
                                         "headers": [{"name": "Authorization", "value": "Bearer {api_key}", "isSecret": True},
                                                     {"name": "X-Team"}]})
    assert option["config"] == {"url": "https://{{tenant}}.example.com/mcp", "transport": "sse",
                                "headers": {"Authorization": "Bearer {{api_key}}", "X-Team": "{{X_Team}}"}}
    assert [(f["key"], f["required"], f["secret"]) for f in option["fields"]] == [
        ("tenant", True, False), ("api_key", False, True), ("X_Team", False, False)]


def test_the_well_known_ones_make_configs_labb_accepts():
    from labb import mcp_client
    known = mcp_registry.featured()
    assert {"github", "time", "filesystem"} <= {s["suggested_name"] for s in known}
    for server in known:
        for option in server["options"]:
            mcp_client.connection(option["config"])
    assert [s["title"] for s in mcp_registry.featured("browser")] == ["Playwright"]


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def registry_item(name, **server):
    return {"server": {"name": name, "description": "d", **server},
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}


@pytest.fixture
def registry(monkeypatch):
    asked = []
    data = {"servers": [
        registry_item("ai.smithery/copy", remotes=[{"type": "streamable-http", "url": "https://server.smithery.ai/x/mcp"}]),
        registry_item("io.github.acme/postgres", packages=[{"registryType": "npm", "identifier": "acme-pg", "transport": {"type": "stdio"}}]),
        registry_item("io.github.acme/nuget-only", packages=[{"registryType": "nuget", "identifier": "x"}]),
        registry_item("io.github.acme/postgres", packages=[]),
    ], "metadata": {"nextCursor": "abc"}}

    def get(url, params):
        asked.append(params)
        return FakeResponse(data)

    monkeypatch.setattr(mcp_registry.http, "get", get)
    monkeypatch.setattr(mcp_registry, "runtimes", lambda: HAVE)
    mcp_registry._cache.clear()
    return asked


def test_the_catalog_searches_the_registry(client, registry, accounts):
    found = client.get("/admin/mcp/catalog", params={"q": "postgres"}).json()
    # Ones labb can't run are left out, the same server once, re-hosted copies last
    assert [s["id"] for s in found["servers"]] == ["io.github.acme/postgres", "ai.smithery/copy"]
    assert found["next"] == "abc" and found["error"] is None and found["runtimes"] == HAVE
    assert registry[0]["search"] == "postgres" and registry[0]["version"] == "latest"
    # Asked again soon, it's the answer from before
    client.get("/admin/mcp/catalog", params={"q": "postgres"})
    assert len(registry) == 1
    client.get("/admin/mcp/catalog", params={"q": "postgres", "cursor": "abc"})
    assert registry[-1]["cursor"] == "abc"
    # With no search, just the well-known ones, and the registry isn't asked
    empty = client.get("/admin/mcp/catalog").json()
    assert empty["servers"] and all(s["featured"] for s in empty["servers"]) and len(registry) == 2


def test_without_the_registry_the_well_known_ones_still_show(client, monkeypatch, accounts):
    from fastapi.testclient import TestClient
    from labb.server import app as server

    def down(url, params):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(mcp_registry.http, "get", down)
    mcp_registry._cache.clear()
    found = client.get("/admin/mcp/catalog", params={"q": "git"}).json()
    assert "Couldn't search the MCP Registry" in found["error"]
    assert {"GitHub", "Git"} <= {s["title"] for s in found["servers"]}
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/mcp/catalog").status_code == 403


def test_one_address_over_two_transports_is_one_way():
    found = mcp_registry.entry({"name": "a/b", "remotes": [{"type": "sse", "url": "https://x.dev/sse"},
                                                           {"type": "streamable-http", "url": "https://x.dev/mcp"}]}, HAVE)
    assert [o["label"] for o in found["options"]] == ["Hosted"]
