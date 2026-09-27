minio server /blobs --console-address :9001 &
S3_PID=$!

# 'local' alias so the compose healthcheck (mc ready local) works.
# Buckets are created by the API on startup (registers.py).
mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 || true

# Readiness loop using mc (no curl/wget in the minimal image)
until mc ready local >/dev/null 2>&1; do
  sleep 1
done

wait $S3_PID
