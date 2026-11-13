"""The web app's addresses: loading /agents in the browser gets the app, fetching it gets the agents."""

import pytest

from labb.server import app as server

NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html,application/xhtml+xml"}
FETCH = {"Sec-Fetch-Mode": "cors", "Accept": "*/*"}


@pytest.fixture
def built(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<div id=root></div>")
    monkeypatch.setattr(server, "WEB_DIST", tmp_path)


@pytest.mark.parametrize("path", ["/agents", "/agents/new", "/chat/abc", "/chat/abc/context/tools", "/studio/workflows/w1/runs/r1", "/settings/keys", "/tasks/t1", "/pcs"])
def test_loading_a_page_gets_the_app(client, built, path):
    response = client.get(path, headers=NAVIGATE)
    assert response.status_code == 200
    assert response.text == "<div id=root></div>"


def test_fetching_from_the_same_address_gets_the_api(client, built):
    response = client.get("/agents", headers=FETCH)
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), list)
    assert "Sec-Fetch-Mode" in response.headers["vary"]
    # Without Fetch Metadata, by what it accepts
    assert isinstance(client.get("/agents", headers={"Accept": "*/*"}).json(), list)
    assert client.get("/agents", headers={"Accept": "text/html"}).text == "<div id=root></div>"


def test_downloads_and_other_addresses_are_left_alone(client, built):
    assert client.get("/skills/nope/download", headers=NAVIGATE).status_code == 404
    assert client.get("/health", headers=NAVIGATE).headers["content-type"].startswith("application/json")


def test_request_bodies_over_the_limit_are_refused_before_theyre_read(client, monkeypatch):
    from labb.server import app as server

    monkeypatch.setattr(server, "MAX_REQUEST_BYTES", 1_000)
    big = b'{"name": "big.txt", "data": "' + b"A" * 2_000 + b'"}'
    # By its Content-Length
    response = client.post("/documents", content=big, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and "over" in response.json()["detail"]

    # Sent in chunks, without a Content-Length
    def chunks():
        for start in range(0, len(big), 500):
            yield big[start:start + 500]

    response = client.post("/documents", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    # Under the limit, it goes through as before
    assert client.get("/documents").status_code == 200
