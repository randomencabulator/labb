#!/bin/sh
# Caddy for labb (compose.yaml's caddy): its Caddyfile's {$APP_SITES} is apps' ports, one site address
# each (https://:9100, https://:9101, …), as a Caddyfile address can't be a range of ports
set -e
port=${APP_PORT_FIRST:-9100}
sites=""
while [ "$port" -le "${APP_PORT_LAST:-9199}" ]; do
	sites="$sites${sites:+, }https://:$port"
	port=$((port + 1))
done
export APP_SITES="$sites"
# Their APIs, each on its app's port + APP_API_PORT_OFFSET
offset=${APP_API_PORT_OFFSET:-100}
port=$(( ${APP_PORT_FIRST:-9100} + offset ))
sites=""
while [ "$port" -le $(( ${APP_PORT_LAST:-9199} + offset )) ]; do
	sites="$sites${sites:+, }https://:$port"
	port=$((port + 1))
done
export API_SITES="$sites"
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
