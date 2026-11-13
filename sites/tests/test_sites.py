"""labb-sites: uploads swapped in whole, what an archive may hold, and the token."""

import io
import os
import stat
import tarfile
import zipfile

import pytest
from fastapi.testclient import TestClient

from labb_sites import app as service

client = TestClient(service.app)
TOKEN = {"Authorization": "Bearer t"}


@pytest.fixture(autouse=True)
def sites(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SITES_DIR", tmp_path)
    monkeypatch.setattr(service, "TOKEN", "t")
    monkeypatch.setattr(service, "TOKEN_FILE", "")
    monkeypatch.setattr(service, "_saved_here", {})
    return tmp_path


def zipped(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def tarred(files: dict[str, str], mode: str = "w:gz") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def put(name: str, body: bytes, headers=TOKEN):
    return client.put(f"/artifacts/{name}", content=body, headers=headers)


def served(sites, name: str, path: str) -> str:
    """What Caddy would serve: the file through the site's link"""

    return (sites / name / path).read_text()


def test_a_zip_becomes_a_site(sites):
    answer = put("blog", zipped({"index.html": "<h1>hi</h1>", "css/site.css": "body{}"}))
    assert answer.status_code == 200, answer.text
    site = answer.json()
    assert site["name"] == "blog" and site["files"] == 2 and site["index"]
    assert site["url"] == "http://testserver/artifacts/blog/"
    assert served(sites, "blog", "index.html") == "<h1>hi</h1>"
    assert (sites / "blog").is_symlink() and not os.readlink(sites / "blog").startswith("/")
    assert stat.S_IMODE((sites / "blog" / "css" / "site.css").stat().st_mode) == 0o644


def test_tars_of_every_kind(sites):
    for mode in ("w", "w:gz", "w:bz2", "w:xz"):
        assert put("t", tarred({"index.html": mode}, mode)).status_code == 200
        assert served(sites, "t", "index.html") == mode


def test_a_zipped_folder_is_the_site_and_junk_stays_out(sites):
    put("docs", zipped({"build/index.html": "home", "build/a/b.html": "b", "__MACOSX/build/._index.html": "x",
                        "build/.DS_Store": "x"}))
    assert served(sites, "docs", "index.html") == "home" and served(sites, "docs", "a/b.html") == "b"
    assert not (sites / "docs" / ".DS_Store").exists()
    assert client.get("/artifacts/docs", headers=TOKEN).json()["files"] == 2


def test_an_upload_replaces_the_site_and_keeps_only_the_one_before(sites):
    for n in range(4):
        put("blog", zipped({"index.html": f"v{n}", f"only-{n}.html": ""}))
    assert served(sites, "blog", "index.html") == "v3"
    assert not (sites / "blog" / "only-2.html").exists()
    assert len(list((sites / ".versions" / "blog").iterdir())) == 2
    assert not list((sites / ".staging").iterdir())


def test_a_refused_upload_leaves_the_site_as_it_was(sites):
    put("blog", zipped({"index.html": "good"}))
    assert put("blog", b"not an archive").status_code == 400
    assert put("blog", zipped({"../escape.html": "x"})).status_code == 400
    assert served(sites, "blog", "index.html") == "good"
    assert not (sites.parent / "escape.html").exists()
    assert not list((sites / ".staging").iterdir())


@pytest.mark.parametrize("name", ["/etc/passwd", "a/../../x", "..", "a/../.."])
def test_paths_outside_the_archive_are_refused(name):
    answer = put("x", tarred({"index.html": "", name: "x"}))
    assert answer.status_code == 400 and "outside" in answer.json()["detail"]


def test_links_and_devices_are_refused(sites):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        link = tarfile.TarInfo("secret")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(link)
    answer = put("x", buffer.getvalue())
    assert answer.status_code == 400 and "link" in answer.json()["detail"]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("secret")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    assert put("x", buffer.getvalue()).status_code == 400
    assert not (sites / "x").exists()


def test_a_file_and_a_folder_of_one_name_are_refused():
    answer = put("x", tarred({"a": "file", "a/b": "inside"}))
    assert answer.status_code == 400 and "same name" in answer.json()["detail"]


def test_limits(monkeypatch):
    monkeypatch.setattr(service, "MAX_FILES", 2)
    assert put("x", zipped({"a": "", "b": "", "c": ""})).status_code == 413
    monkeypatch.setattr(service, "MAX_FILES", 100)
    monkeypatch.setattr(service, "MAX_BYTES", 1000)
    # Small as an archive, big unpacked: counted as it's written
    bomb = zipfile.ZipFile(buf := io.BytesIO(), "w", zipfile.ZIP_DEFLATED)
    bomb.writestr("index.html", "a" * 5000)
    bomb.close()
    assert len(buf.getvalue()) < 1000
    assert put("x", buf.getvalue()).status_code == 413
    assert put("x", b"x" * 2000).status_code == 413
    assert put("x", b"").status_code == 400
    assert put("x", zipped({})).status_code == 400


@pytest.mark.parametrize("name", ["Blog", "-a", "a-", "_api", ".versions", "a.b", "x" * 64])
def test_names(name):
    assert put(name, zipped({"index.html": ""})).status_code in (400, 404, 405)


def test_list_get_and_delete(sites):
    put("b", zipped({"index.html": "b"}))
    put("a", zipped({"page.html": "a"}))
    listed = client.get("/artifacts", headers=TOKEN).json()["artifacts"]
    assert [site["name"] for site in listed] == ["a", "b"]
    assert listed[0]["index"] is False and listed[1]["url"] == "http://testserver/artifacts/b/"
    assert client.delete("/artifacts/b", headers=TOKEN).json() == {"deleted": "b"}
    assert not (sites / "b").exists() and not (sites / ".versions" / "b").exists()
    assert client.delete("/artifacts/b", headers=TOKEN).status_code == 404
    assert client.get("/artifacts/b", headers=TOKEN).status_code == 404


def test_the_url_is_where_the_request_came_to(monkeypatch):
    forwarded = {**TOKEN, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "labb.lan:8301"}
    assert put("a", zipped({"index.html": ""}), forwarded).json()["url"] == "https://labb.lan:8301/artifacts/a/"
    monkeypatch.setattr(service, "PUBLIC_URL", "https://sites.example.com")
    assert client.get("/artifacts/a", headers=TOKEN).json()["url"] == "https://sites.example.com/artifacts/a/"


def test_everything_but_health_needs_the_token():
    assert client.get("/health").status_code == 200
    assert client.get("/artifacts").status_code == 401
    assert client.get("/artifacts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert put("a", zipped({"index.html": ""}), {}).status_code == 401
    assert client.delete("/artifacts/a").status_code == 401


def test_tidy_clears_what_a_cut_off_upload_left(sites):
    put("kept", zipped({"index.html": ""}))
    (sites / ".staging" / "half").mkdir(parents=True)
    (sites / ".versions" / "gone" / "x").mkdir(parents=True)
    os.symlink(".versions/gone/x", sites / ".gone.new")
    service.tidy()
    assert not (sites / ".staging").exists() and not (sites / ".gone.new").is_symlink()
    assert not (sites / ".versions" / "gone").exists()
    assert served(sites, "kept", "index.html") == ""


def test_the_token_is_made_once(tmp_path, monkeypatch):
    monkeypatch.delenv("SITES_TOKEN", raising=False)
    monkeypatch.setattr(service, "TOKEN_FILE", str(tmp_path / "run" / "token"))
    made = service.load_token()
    assert made and service.load_token() == made
    assert stat.S_IMODE((tmp_path / "run" / "token").stat().st_mode) == 0o600


def test_the_one_kept_is_the_one_just_before(sites):
    for n in range(5):
        put("fast", zipped({"index.html": f"v{n}"}))
    kept = sorted((sites / ".versions" / "fast").iterdir())
    assert [(path / "index.html").read_text() for path in kept] == ["v3", "v4"]
    assert served(sites, "fast", "index.html") == "v4"


def test_an_admin_changes_the_limits_from_labb(monkeypatch, tmp_path):
    shown = {limit["key"]: limit for limit in client.get("/limits", headers=TOKEN).json()}
    assert shown["site_files"]["value"] == service.MAX_FILES and shown["site_mb"]["unit"] == "MB"
    assert client.get("/limits").status_code == 401
    assert client.put("/limits", json={"site_files": 2}, headers=TOKEN).status_code == 200
    assert put("x", zipped({"a": "", "b": "", "c": ""})).status_code == 413
    assert client.put("/limits", json={"site_files": 0}, headers=TOKEN).status_code == 400
    # Kept next to the token, when there's a file for it
    monkeypatch.setattr(service, "TOKEN_FILE", str(tmp_path / "token"))
    client.put("/limits", json={"site_files": 5}, headers=TOKEN)
    assert '"site_files": 5' in (tmp_path / "limits.json").read_text()
    client.put("/limits", json={"site_files": None}, headers=TOKEN)
    assert put("x", zipped({"a": "", "b": "", "c": ""})).status_code == 200
