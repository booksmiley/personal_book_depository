# Production image for Render: Litestream + the Flask app.
#
# Litestream restores the SQLite DB from object storage (Cloudflare R2 / Backblaze
# B2) on boot, then runs gunicorn while continuously streaming the write-ahead log
# back up. Render's disk is just a working copy; the bucket is the source of truth.
FROM python:3.12-slim

# --- Litestream binary ---
ARG LITESTREAM_VERSION=v0.3.13
ADD https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-amd64.tar.gz /tmp/litestream.tar.gz
RUN tar -C /usr/local/bin -xzf /tmp/litestream.tar.gz && rm /tmp/litestream.tar.gz

# --- App ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# SQLite lives here; Litestream replicates this directory's DB file. Keep it OUT of
# /app so a code redeploy never touches the data dir.
ENV BOOK_DATA_DIR=/data
RUN mkdir -p /data

COPY litestream.yml /etc/litestream.yml
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
