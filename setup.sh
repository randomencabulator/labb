#!/bin/sh
# First start of the server: makes .env from .env.example, with a random Postgres password.
# Safe to run again: it never changes a .env setting that's already there.
#
#   ./setup.sh                          the above
#   ./setup.sh dev                      also build labb's images from this checkout (compose.dev.yaml)
#                                       instead of pulling a release's
#   ./setup.sh https [name|address…]    also https on your network, with Caddy's own CA
#                                       (default names: labb.lan and this machine's LAN address)
#   ./setup.sh domain <domain> <email>  also https for a public domain, with Let's Encrypt
#   ./setup.sh root                     once Caddy has started: its root certificate as
#                                       ./caddy-root.crt, to trust on each device
set -eu
cd "$(dirname "$0")"

# NAME=value in .env, unless NAME is already set there
set_once() {
    if grep -q "^$1=" .env; then
        echo "$1 is already set in .env ($(grep "^$1=" .env | tail -1 | cut -d= -f2-)); kept."
    else
        printf '%s=%s\n' "$1" "$2" >> .env
        echo "Set $1=$2"
    fi
}

# This machine's address on the network (the one its default route leaves from)
lan_address() {
    if command -v ip >/dev/null 2>&1; then
        ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}'
    elif hostname -I >/dev/null 2>&1; then
        hostname -I | awk '{ print $1 }'
    elif command -v ipconfig >/dev/null 2>&1; then
        ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
    fi
}

# Turns the https profile on, keeping the profiles .env already has
https_profile() {
    if ! grep -q '^COMPOSE_PROFILES=' .env; then
        set_once COMPOSE_PROFILES https
    elif grep '^COMPOSE_PROFILES=' .env | tail -1 | cut -d= -f2- | tr ',' '\n' | grep -qx https; then
        echo "COMPOSE_PROFILES already has https."
    else
        awk '/^COMPOSE_PROFILES=/ { sub(/[[:space:]]*$/, ""); $0 = $0 ",https" } { print }' .env > .env.new && mv .env.new .env
        echo "Added https to COMPOSE_PROFILES."
    fi
}

# What https needs either way: plain http only on this machine, and the server believing Caddy
https_common() {
    https_profile
    set_once LABB_BIND 127.0.0.1
    # The sites too: Caddy serves them over https on SITES_HTTPS_PORT
    set_once SITES_BIND 127.0.0.1
    set_once FORWARDED_ALLOW_IPS '"*"'
    set_once LABB_PUBLIC_URL "https://$1"
}

# How to start it: building the images from the source, or pulling a release's
up_command() {
    if grep -q '^COMPOSE_FILE=.*compose\.dev\.yaml' .env 2>/dev/null; then
        echo "docker compose up -d --build"
    else
        echo "docker compose up -d"
    fi
}

base() {
    if [ ! -f .env ]; then
        cp .env.example .env
        echo "Made .env from .env.example."
    fi

    if grep -q '^POSTGRES_PASSWORD=.' .env; then
        echo "POSTGRES_PASSWORD is already set in .env."
    elif docker volume inspect labb_postgres-data >/dev/null 2>&1; then
        # Postgres keeps the password it was first started with; a new one in .env would lock the server out
        echo "The database already exists with the default password (labb), so .env keeps it."
        echo "To change it: docker compose exec postgres psql -U labb -c \"ALTER USER labb PASSWORD 'new one'\","
        echo "then put POSTGRES_PASSWORD=<new one> in .env and run docker compose up -d."
    else
        password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
        printf '\n# Made by setup.sh\nPOSTGRES_PASSWORD=%s\n' "$password" >> .env
        echo "Put a random POSTGRES_PASSWORD in .env."
    fi
}

case "${1:-}" in
"")
    base
    echo "Start it with: $(up_command)"
    ;;
dev)
    if [ ! -f compose.dev.yaml ]; then
        echo "No compose.dev.yaml here: building from the source needs a git checkout of labb." >&2
        exit 1
    fi
    base
    set_once COMPOSE_FILE compose.yaml:compose.dev.yaml
    echo "Start it with: $(up_command)"
    ;;
https)
    base
    shift
    if [ $# -gt 0 ]; then
        names="$*"
    else
        address=$(lan_address)
        names="labb.lan${address:+ $address}"
    fi
    first=${names%% *}
    grep -q '^LABB_HOSTNAME=' .env || printf '\n# https on your network (setup.sh https)\n' >> .env
    https_common "$first"
    set_once LABB_HOSTNAME "\"$(echo "$names" | sed 's/ /, /g')\""
    # A browser given an address asks for no name: it gets the address's certificate
    for name in $names; do
        case "$name" in
        *[!0-9.]*) ;;
        *) set_once LABB_DEFAULT_SNI "$name"; break ;;
        esac
    done
    echo
    echo "Next:"
    echo "  1. Make $first point at this machine: in your router's DNS (best), or in each device's hosts file."
    echo "  2. $(up_command)"
    echo "  3. ./setup.sh root, and trust caddy-root.crt on each device (docs/guide.md, HTTPS)."
    echo "Then open https://$first"
    ;;
domain)
    base
    if [ $# -ne 3 ]; then
        echo "Usage: ./setup.sh domain <domain> <email>, e.g. ./setup.sh domain labb.example.com you@example.com" >&2
        exit 1
    fi
    grep -q '^LABB_HOSTNAME=' .env || printf '\n# https for a public domain (setup.sh domain)\n' >> .env
    https_common "$2"
    set_once LABB_HOSTNAME "$2"
    set_once LABB_TLS "$3"
    echo
    echo "Next: point $2's DNS at this machine and forward ports 80 and 443 to it (Let's Encrypt checks"
    echo "the domain over them), then $(up_command) and open https://$2"
    ;;
root)
    docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt
    chmod 644 caddy-root.crt
    echo "Saved caddy-root.crt. Trust it on each device (docs/guide.md, HTTPS)."
    if command -v openssl >/dev/null 2>&1; then
        echo "Check it's this one on the device: $(openssl x509 -in caddy-root.crt -noout -fingerprint -sha256)"
    fi
    ;;
*)
    sed -n '5,12p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 1
    ;;
esac
