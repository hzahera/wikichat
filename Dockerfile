FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && useradd --create-home --uid 10001 wikichat \
 && mkdir -p /data && chown wikichat:wikichat /data

USER wikichat
ENV WIKICHAT_HOST=0.0.0.0 WIKICHAT_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["wikichat", "serve"]
