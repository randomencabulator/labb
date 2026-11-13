# labb-sandbox

Runs the Python steps of [labb](../README.md)'s Studio workflows, isolated from the server.

`POST /run` with `{"code": "...", "inputs": {...}, "timeout": 30}` runs the code with `inputs` (the run's
inputs and every step's output so far) and answers `{"ok": true, "result": ..., "stdout": "..."}`, where
`result` is what the code set it to, or `{"ok": false, "error": "<traceback>"}`.

Each run is a fresh `python -I` in a folder of its own, deleted afterwards, with limits on CPU time,
memory (256 MB), processes, file size and open files; when its time is up it's killed with anything it
started. Only the standard library is there.

In Docker (`compose.yaml`) it's also:

- **off the internet:** it's only on an internal network that the server reaches;
- **read-only**, apart from a small `/tmp`;
- **a user per run:** the service runs as root inside the container only to give each run a user of its
  own (20000 and up), so runs can't see each other; every capability but the few that needs is dropped,
  and no process can gain more.

Run from a checkout (`uv run labb-sandbox`, on :4000), the limits apply but there's no network
isolation and every run is your user: use it for development only.

| Setting | Default | |
| --- | --- | --- |
| `SANDBOX_TOKEN` | | Require `Authorization: Bearer <token>` (the server sends `SANDBOX_TOKEN` too) |
| `SANDBOX_TOKEN_FILE` | | Without `SANDBOX_TOKEN`: the token kept in this file, made on the first start (mode 600, owned by `SANDBOX_TOKEN_OWNER`, default 1000: the server's user). `compose.yaml` shares it with the server on a volume |
| `SANDBOX_MEMORY_MB` | `256` | Memory per run |
| `SANDBOX_JOBS` | `4` | Runs at once |
| `PORT` | `4000` | |

Tests: `uv run pytest`.
