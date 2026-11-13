# The front of labb-sites: Caddy serving each site at /artifacts/<name>/ from the sites volume (read-only), and
# passing /_api/ to the service that takes uploads (see ../Caddyfile's comments and compose.yaml)
FROM caddy:2.10.2-alpine
# A copy of the binary without the image's file capability, which a container with every
# capability dropped couldn't run; it only listens on 8080
RUN cp /usr/bin/caddy /usr/bin/caddy.plain && mv /usr/bin/caddy.plain /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
ENV XDG_CONFIG_HOME=/tmp/config XDG_DATA_HOME=/tmp/data
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
