# The app's server: backend/, run by uvicorn on port 8080. It's built from the app's folder so it can
# copy db/, but it's only the server: the page (frontend/) is built and served apart, by itself.
# Packages go in backend/requirements.txt, with exact versions.
FROM python:3.14-alpine
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check -r requirements.txt
COPY backend/ ./
COPY db/ ./db/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
