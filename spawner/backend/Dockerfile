# An app's API (labb-spawner starts one per app that turns it on): gateway.py, with PostgREST inside,
# on port 8080. PostgREST is one static binary, copied from its official image (pinned); psycopg
# (pinned, its own wheel with libpq) is sign-in's way to the database.
FROM postgrest/postgrest:v16.5 AS postgrest

FROM python:3.14-alpine
COPY --from=postgrest /bin/postgrest /usr/local/bin/postgrest
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore "psycopg[binary]==3.3.8"
COPY gateway.py client.js /srv/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python", "-I", "/srv/gateway.py"]
