# The front of every app (app.py's front_image): Caddy, serving the app's built page from /srv/current
# (the spawner's pages.py swaps it for each new build) and passing /api/ to the app's server, if any.
FROM caddy:2.10.2-alpine
# A copy of the binary without the image's file capability, which a container with every
# capability dropped couldn't run; it only listens on 8080
RUN cp /usr/bin/caddy /usr/bin/caddy.plain && mv /usr/bin/caddy.plain /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
ENV XDG_CONFIG_HOME=/tmp/config XDG_DATA_HOME=/tmp/data
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
