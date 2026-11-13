"""A stand-in for Docker, and the spawner's API with a token, for the tests"""

import pytest
from docker.errors import APIError, ImageNotFound, NotFound
from fastapi.testclient import TestClient

from labb_spawner import app as spawner


class FakeContainer:
    def __init__(self, docker, name, labels, published=None):
        self.docker, self.name, self.labels, self.status = docker, name, labels, "running"
        ports = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(published)}]} if published else {}
        self.attrs = {"NetworkSettings": {"Ports": ports}, "State": {"ExitCode": 0, "OOMKilled": False}, "RestartCount": 0}
        # What its network has carried, and what it wrote
        self.bytes, self.output, self.memory = 1000, b"2026-11-08T12:00:00Z serving on 8080\n", 30_000_000
        self.port_taken = False
        self.exit_code = 0

    def reload(self):
        pass

    def remove(self, force=False):
        if self in self.docker.running:
            self.docker.running.remove(self)

    def stop(self, timeout=10):
        self.status = "exited"
        self.attrs["State"]["ExitCode"] = 143

    def start(self):
        if self.port_taken:
            raise APIError("Bind for 0.0.0.0:9100 failed: port is already allocated")
        # On a network made again since, as Docker says it
        for name, joined in self.attrs["NetworkSettings"].get("Networks", {}).items():
            current = self.docker.networks.ids.get(name)
            if current is None or current.id != joined["NetworkID"]:
                raise APIError(f"network {joined['NetworkID']} not found")
        self.status = "running"

    def restart(self, timeout=10):
        self.status = "running"

    def logs(self, tail=None, timestamps=False, stdout=True, stderr=True):
        self.docker.log_asks.append(tail)
        return self.output

    def wait(self, timeout=None):
        return {"StatusCode": self.exit_code}

    def stats(self, stream, one_shot):
        return {"networks": {"eth0": {"rx_bytes": self.bytes, "tx_bytes": 0}}, "memory_stats": {"usage": self.memory}}


class FakeContainers:
    def __init__(self, docker):
        self.docker = docker

    def list(self, all=False, filters=None):
        def matches(container):
            for want in filters["label"]:
                key, _, value = want.partition("=")
                if key not in container.labels or (value and container.labels[key] != value):
                    return False
            return True

        return [c for c in self.docker.running if matches(c)]

    def run(self, image, command=None, name=None, labels=None, ports=None, **kwargs):
        if "labb.app.page-build" in (labels or {}):
            return self.build_page(kwargs["volumes"], labels)
        if "labb.app.artifact-build" in (labels or {}):
            return self.build_artifact(image, kwargs, labels)
        port = next(iter(ports.values()))[1] if ports else None
        if port in self.docker.busy:
            # Docker makes the container, then fails to publish it
            self.docker.running.append(FakeContainer(self.docker, name, {}))
            raise APIError("Bind for 0.0.0.0 failed: port is already allocated")
        if image in self.docker.broken:
            raise APIError("the new image won't start")
        if any(c.name == name for c in self.docker.running):
            raise APIError(f'Conflict. The container name "/{name}" is already in use')
        self.docker.runs.append({"image": image, "ports": ports, "labels": labels, **kwargs})
        container = FakeContainer(self.docker, name, labels, port)
        if image in self.docker.crashing:
            # Started, then went down at once, and Docker starts it again and again
            container.status, container.attrs["RestartCount"] = "restarting", 2
            container.attrs["State"]["ExitCode"] = self.docker.crash_codes.get(image, 1)
            container.output = self.docker.crashing[image]
        if network := kwargs.get("network"):
            joined = self.docker.networks.ids.get(network)
            container.attrs["NetworkSettings"]["Networks"] = {network: {"NetworkID": joined.id if joined else None}}
        self.docker.running.append(container)
        return container

    def build_page(self, volumes, labels):
        """npm in the page's work folder: a file with FAIL in it fails, as a syntax error would;
        else dist/ with an index.html that says which App.jsx it was built from"""
        from pathlib import Path
        work = next(iter(volumes))
        if self.docker.mounted:
            # The host's path, as the spawner's own
            work = work.replace(*self.docker.mounted, 1)
        work = Path(work)
        self.docker.page_builds.append(labels["labb.app.page-build"])
        box = FakeContainer(self.docker, "page-build", labels)
        files = [p for p in work.rglob("*") if p.is_file() and "node_modules" not in p.parts]
        if any("FAIL" in p.read_text(errors="replace") for p in files):
            box.exit_code = 1
            box.output = (b"vite v8 building client environment for production...\nerror during build:\n"
                          b"[builtin:vite-transform] Expected `}` but found `EOF`\n  src/App.jsx:1:31\n"
                          b"    at aggregateBindingErrorsIntoJsError (file:///work/node_modules/rolldown/x.mjs:50:18)\n")
            return box
        (work / "node_modules").mkdir(exist_ok=True)
        (work / "dist").mkdir()
        app = work / "src" / "App.jsx"
        (work / "dist" / "index.html").write_text(app.read_text() if app.is_file() else "<!doctype html>")
        box.output = "vite v8 building client environment for production...\n\u2713 built in 1s\n".encode()
        return box

    def build_artifact(self, image, kwargs, labels):
        """next build: a file with FAIL in it fails as a type error would; else out/index.html with
        the page's text, under the base path it was given"""
        from pathlib import Path
        src, out = (Path(path.replace(*self.docker.mounted, 1) if self.docker.mounted else path) for path in kwargs["volumes"])
        self.docker.artifact_builds.append({"image": image, "name": labels["labb.app.artifact-build"], **kwargs})
        box = FakeContainer(self.docker, "artifact-build", labels)
        failing = [p for p in src.rglob("*") if p.is_file() and "FAIL" in p.read_text(errors="replace")]
        if failing:
            box.exit_code = 1
            box.output = (b"\xe2\x96\xb2 Next.js 16.4.6 (Turbopack)\n  Running TypeScript ...\n"
                          b"app/page.tsx(3,30): error TS2820: Type '\"mnth\"' is not assignable to type '\"month\"'.\n"
                          b"Failed to type check.\n")
            return box
        (out / "index.html").write_text(f"{kwargs['environment']['ARTIFACT_BASE_PATH']}\n{(src / 'app' / 'page.tsx').read_text()}")
        (out / "_next").mkdir()
        (out / "_next" / "page.js").write_text("js")
        box.output = b"\xe2\x9c\x93 Generating static pages (4/4)\n"
        return box

    def get(self, name):
        found = next((c for c in self.docker.running if c.name == name), None)
        if found is None:
            raise NotFound(name)
        return found


class FakeImage:
    def __init__(self, tags):
        self.tags = tags


class FakeImages:
    def __init__(self, docker):
        self.docker = docker

    def list(self, filters):
        app_id = filters["label"].split("=", 1)[1]
        return [FakeImage([tag]) for tag in sorted(self.docker.tags) if tag.startswith(f"labb-app-{app_id}:")]

    def remove(self, tag, noprune=False):
        self.docker.tags.discard(tag)

    def get(self, tag):
        if tag not in self.docker.tags:
            raise ImageNotFound(tag)
        return FakeImage([tag])


class FakeNetwork:
    def __init__(self, name, number):
        self.name, self.id = name, f"net-{name}-{number}"


class FakeNetworks:
    """Networks by name; one made again gets another id, as Docker's do"""

    def __init__(self):
        self.made, self.ids = [], {}

    def get(self, name):
        if name not in self.ids:
            raise NotFound(name)
        return self.ids[name]

    def create(self, name, driver):
        self.made.append(name)
        self.ids[name] = FakeNetwork(name, len(self.made))

    def remake(self, name):
        """The network removed and made again (compose down and up)"""
        self.create(name, "bridge")


class FakeApi:
    def __init__(self, docker):
        self.docker = docker

    def create_endpoint_config(self, aliases):
        return {"Aliases": aliases}

    def build(self, path, tag, decode, **kwargs):
        """Reads the Dockerfile it was given: a line `FAIL` fails the build"""
        from pathlib import Path
        self.docker.builds.append({"path": path, "tag": tag, **kwargs})
        dockerfile = (Path(path) / kwargs.get("dockerfile", "Dockerfile")).read_text()
        yield {"stream": "Step 1/2 : FROM busybox\n"}
        yield {"status": "Pulling from library/busybox", "id": "latest"}
        yield {"status": "Downloading", "progressDetail": {"current": 1, "total": 2}, "id": "abc"}
        if "FAIL" in dockerfile:
            yield {"errorDetail": {"message": "exit 1"}, "error": "The command '/bin/sh -c false' returned a non-zero code: 1"}
            return
        yield {"stream": "Successfully built\n"}
        self.docker.tags.add(tag)


class FakeDocker:
    def __init__(self):
        self.running, self.runs, self.busy, self.log_asks = [], [], set(), []
        self.builds, self.tags, self.broken, self.page_builds = [], set(), set(), []
        self.artifact_builds = []
        # (the host's path, the spawner's) of its data, when it runs in a container
        self.mounted = None
        # Images whose containers crash as they start, with what they write
        self.crashing, self.crash_codes = {}, {}
        self.containers, self.images, self.networks = FakeContainers(self), FakeImages(self), FakeNetworks()
        self.api = FakeApi(self)

    def ping(self):
        return True


@pytest.fixture
def docker(monkeypatch, tmp_path):
    fake = FakeDocker()
    monkeypatch.setattr(spawner.sources, "DATA", tmp_path)
    monkeypatch.setattr(spawner, "_host_data", None)
    monkeypatch.setattr(spawner, "_docker", fake)
    monkeypatch.setattr(spawner, "TOKEN", "secret")
    monkeypatch.setattr(spawner, "NETWORK", "")
    monkeypatch.setattr(spawner, "API_WAIT_SECONDS", 0)
    monkeypatch.setattr(spawner, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(spawner, "PUBLISH", True)
    monkeypatch.setattr(spawner, "_stopped_by", {})
    monkeypatch.setattr(spawner, "_traffic", {})
    # Pages build when asked (pages.settle); a test that wants them to build by themselves says how soon
    monkeypatch.setattr(spawner.pages, "DEBOUNCE_SECONDS", 3600)
    yield fake
    with spawner.pages._lock:
        for timer in spawner.pages._timers.values():
            timer.cancel()
        spawner.pages._timers.clear()
        spawner.pages._building.clear()


@pytest.fixture
def http(docker):
    return TestClient(spawner.app, headers={"Authorization": "Bearer secret"})
