"""Installing labb on other PCs from the server: /version, /wheels and /install.sh."""

import pytest
from fastapi.testclient import TestClient

from labb.server import install
from labb.server import app as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Installing needs no account: the server, called by a PC that isn't logged in"""
    monkeypatch.setattr(install, "WHEELS_DIR", tmp_path)
    return TestClient(server.app, base_url="http://192.168.1.50:8000")


def built_wheel(folder):
    path = folder / f"labb-{install.version()}-py3-none-any.whl"
    path.write_bytes(b"wheel")
    return path


def test_without_a_built_wheel_there_is_nothing_to_install(client):
    assert client.get("/version").json() == {"version": install.version(), "wheel": None}
    assert client.get("/install.sh").status_code == 404


def test_the_install_script_installs_the_wheel_and_writes_the_addresses(client, tmp_path):
    wheel = built_wheel(tmp_path)
    assert client.get("/version").json()["wheel"] == wheel.name

    script = client.get("/install.sh").text
    assert script.startswith("#!/bin/sh")
    assert f"--python 3.14 http://192.168.1.50:8000/wheels/{wheel.name}" in script
    assert "--refresh-package labb" in script
    # Model calls and page reads go through the server
    assert "LABB_SERVER=http://192.168.1.50:8000\nOLLAMA_HOST=http://192.168.1.50:8000/ollama\nCRAWLER_URL=http://192.168.1.50:8000/crawler" in script
    assert "labb --login" in script

    assert client.get(f"/wheels/{wheel.name}").content == b"wheel"
    assert client.get("/wheels/other-0.0.1-py3-none-any.whl").status_code == 404
