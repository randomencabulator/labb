#!/bin/bash
# labb's backups (the `backup` service in compose.yaml): every night at BACKUP_AT (in TZ), the
# database as SQL and the key its secrets are encrypted with, in a folder of their own under
# /backups (./backups on the host), keeping the newest BACKUP_KEEP. With --once, one backup now.
#
# Restore: docker compose up -d postgres, then
#   gunzip -c backups/labb-<time>/labb.sql.gz | docker compose exec -T postgres psql -U labb labb
# and put secret.key back in the labb-data volume before docker compose up -d. With https, put
# caddy-ca/root.crt and root.key back in the caddy-data volume (caddy/pki/authorities/local/) too,
# so the devices that trust it keep doing so. People's apps (their files, settings with the key
# their secrets are encrypted with, and data) are apps.tar.gz: unpack it into the spawner-data volume,
#   docker run --rm -v labb_spawner-data:/spawner -v "$PWD/backups/labb-<time>":/b alpine tar xzpf /b/apps.tar.gz -C /spawner
# then docker compose up -d, and rebuild each app from its page (its images aren't in the backup).
# The sites are sites.tar.gz: unpack it into the sites-data volume the same way,
#   docker run --rm -v labb_sites-data:/sites -v "$PWD/backups/labb-<time>":/b alpine tar xzpf /b/sites.tar.gz -C /sites
# Apps' databases are apps-db.sql.gz (every app's, with their roles): after docker compose up -d,
#   gunzip -c backups/labb-<time>/apps-db.sql.gz | docker compose exec -T apps-postgres psql -U labb_apps postgres
#
# Encrypted (recommended: a backup holds secret.key, which opens every key and token labb keeps):
# set BACKUP_AGE_RECIPIENT to an age public key (age-keygen -o labb-backup.key prints it; keep that
# key file off this server). Each file is then <name>.age, and restoring starts with
#   age -d -i labb-backup.key backups/labb-<time>/labb.sql.gz.age > labb.sql.gz
set -euo pipefail

DIR=${BACKUP_DIR:-/backups}
KEEP=${BACKUP_KEEP:-7}
AT=${BACKUP_AT:-03:00}
KEY=${SECRET_KEY_FILE:-/data/secret.key}
# Caddy's own CA (the https profile), which the devices on your network trust
CA=${CADDY_CA_DIR:-/caddy/caddy/pki/authorities/local}
# The spawner's volume: people's apps
APPS=${APPS_DIR:-/spawner}
# The sites (labb-sites)
SITES=${SITES_DIR:-/sites}
# The apps' Postgres, and where its admin password is
APPS_PGHOST=${APPS_PGHOST:-}
APPS_PGPASSWORD_FILE=${APPS_PGPASSWORD_FILE:-}
# An age public key (age1…) to encrypt each backup with
AGE_RECIPIENT=${BACKUP_AGE_RECIPIENT:-}

# age, which the Postgres image doesn't have: installed once, when it's wanted
need_age() {
    command -v age >/dev/null 2>&1 && return
    echo "labb-backup: installing age to encrypt the backups"
    apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends age >/dev/null && rm -rf /var/lib/apt/lists/*
}

# Every file of a backup as <file>.age, the plain ones gone
encrypt() {
    find "$1" -type f ! -name '*.age' | while read -r file; do
        age -r "$AGE_RECIPIENT" -o "$file.age" "$file" && rm -f "$file"
    done
}

backup() {
    stamp=$(date +%Y-%m-%dT%H%M%S)
    # Two in the same second (--once by hand) mustn't land in one folder
    base=$stamp n=2
    while [ -e "$DIR/labb-$stamp" ]; do stamp="$base-$n"; n=$((n + 1)); done
    partial="$DIR/.labb-$stamp.partial"
    mkdir -p "$partial"
    # A dump that fails leaves no backup behind, so a broken one is never kept in place of a good one
    if ! pg_dump -h "${PGHOST:-postgres}" -U "${PGUSER:-labb}" -d "${PGDATABASE:-labb}" | gzip > "$partial/labb.sql.gz"; then
        rm -rf "$partial"
        echo "labb-backup: the dump failed; no backup made" >&2
        return 1
    fi
    if [ -f "$KEY" ]; then
        cp "$KEY" "$partial/secret.key"
        chmod 600 "$partial/secret.key"
    fi
    if [ -d "$APPS/apps" ]; then
        # Owners and modes kept (an app's data is its own user's), links kept as links
        if ! tar czpf "$partial/apps.tar.gz" -C "$APPS" .; then
            rm -rf "$partial"
            echo "labb-backup: packing the apps failed; no backup made" >&2
            return 1
        fi
        chmod 600 "$partial/apps.tar.gz"
    fi
    # Each site's link and the version it points at (not the one before, kept only for a moment)
    served=$(cd "$SITES" 2>/dev/null && for link in *; do [ -L "$link" ] && printf './%s\n./%s\n' "$link" "$(readlink "$link")"; done || true)
    if [ -n "$served" ]; then
        if ! printf '%s\n' "$served" | tar czpf "$partial/sites.tar.gz" -C "$SITES" -T -; then
            rm -rf "$partial"
            echo "labb-backup: packing the sites failed; no backup made" >&2
            return 1
        fi
    fi
    if [ -n "$APPS_PGHOST" ] && [ -f "$APPS_PGPASSWORD_FILE" ]; then
        if ! PGPASSWORD="$(cat "$APPS_PGPASSWORD_FILE")" pg_dumpall -h "$APPS_PGHOST" -U labb_apps | gzip > "$partial/apps-db.sql.gz"; then
            rm -rf "$partial"
            echo "labb-backup: the apps' databases' dump failed; no backup made" >&2
            return 1
        fi
        chmod 600 "$partial/apps-db.sql.gz"
    fi
    if [ -f "$CA/root.key" ]; then
        mkdir -p "$partial/caddy-ca"
        cp "$CA"/root.crt "$CA"/root.key "$partial/caddy-ca/"
        chmod 600 "$partial/caddy-ca/root.key"
    fi
    if [ -n "$AGE_RECIPIENT" ]; then
        if ! encrypt "$partial"; then
            rm -rf "$partial"
            echo "labb-backup: encrypting failed; no backup made" >&2
            return 1
        fi
    fi
    # Owned by your user on the host (e.g. BACKUP_OWNER=1000:1000), rather than root
    [ -z "${BACKUP_OWNER:-}" ] || chown -R "$BACKUP_OWNER" "$partial"
    mv "$partial" "$DIR/labb-$stamp"
    echo "labb-backup: $DIR/labb-$stamp ($(du -sh "$DIR/labb-$stamp" | cut -f1))"
    # The newest KEEP stay
    ls -1d "$DIR"/labb-* 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
        rm -rf "$old"
        echo "labb-backup: removed $old"
    done
}

if [ "${1:-}" = "--once" ]; then
    [ -z "$AGE_RECIPIENT" ] || need_age
    backup
    exit
fi

if [ -n "$AGE_RECIPIENT" ]; then
    need_age
else
    echo "labb-backup: backups aren't encrypted (they hold secret.key); set BACKUP_AGE_RECIPIENT to encrypt them" >&2
fi
mkdir -p "$DIR"
# The first time, right away: a new server shouldn't wait a day for its first backup
if ! ls -1d "$DIR"/labb-* >/dev/null 2>&1; then
    backup || true
fi
while true; do
    now=$(date +%s)
    next=$(date -d "$AT" +%s)
    [ "$next" -gt "$now" ] || next=$(date -d "tomorrow $AT" +%s)
    echo "labb-backup: next at $(date -d "@$next" '+%Y-%m-%d %H:%M %Z')"
    sleep $((next - now))
    backup || true
done
