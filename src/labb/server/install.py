"""Installing labb on other PCs from the web chat's server:
the wheel to install, its version, and the install script.

A PC runs `curl -fsSL http://<server>:8000/install.sh | sh`. The script installs uv if
needed, installs the wheel this server hands out as a uv tool, and writes the server's
addresses into ~/.labb/.env, keeping anything else that's there (like the API key
`labb --login` saves). Model calls and page reads go through the server too (see
gateway.py), so those addresses are the server's.
"""

import os
import shlex
import tomllib
from pathlib import Path

# The project dir, above src/labb/server
SOURCE_DIR = Path(__file__).resolve().parents[3]
# Where the wheels are: the Docker image builds one there (`uv build --wheel --out-dir wheels`)
WHEELS_DIR = Path(os.getenv("WHEELS_DIR", SOURCE_DIR / "wheels"))
def version() -> str:
    """The version in pyproject.toml (the server runs from the source, not an installed copy)"""
    return tomllib.loads((SOURCE_DIR / "pyproject.toml").read_text())["project"]["version"]


def wheel() -> Path | None:
    """The wheel of this version, if it's been built"""

    name = f"labb-{version()}-py3-none-any.whl"
    path = WHEELS_DIR / name
    return path if path.is_file() else None


def script(server_url: str, wheel_name: str, settings: dict[str, str]) -> str:
    """The install script: install uv if needed, install labb from this server, and
    write `settings` into ~/.labb/.env"""

    keys = "|".join(settings)
    lines = "\n".join(f"{key}={value}" for key, value in settings.items())
    return f"""#!/bin/sh
# Installs labb from {server_url}
set -eu

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (https://docs.astral.sh/uv/)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing labb {version()}…"
# --refresh-package: the server's wheel, not one uv cached from the same address before
uv tool install --force --refresh-package labb --python 3.14 {shlex.quote(f"{server_url}/wheels/{wheel_name}")}

# The server's addresses; anything else in the file stays
mkdir -p "$HOME/.labb"
env_file="$HOME/.labb/.env"
touch "$env_file"
grep -Ev '^({keys})=' "$env_file" > "$env_file.new" || true
cat >> "$env_file.new" <<'EOF'
{lines}
EOF
mv "$env_file.new" "$env_file"

echo
if grep -q '^LABB_API_KEY=' "$env_file"; then
  echo "Done. Run labb in any folder; labb --update gets newer versions from this server."
else
  echo "Done. Next, log in to this server: labb --login"
  echo "Then run labb in any folder; labb --update gets newer versions from this server."
fi
echo "Settings: $env_file"
if ! command -v labb >/dev/null 2>&1; then
  echo "labb isn't on your PATH yet: run 'uv tool update-shell' and open a new terminal."
fi
"""
