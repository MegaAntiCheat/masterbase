#!/bin/sh
# run alembic here because we are not forwarding the DB
if [ -f /first_run ]; then
    if ! pdm run alembic upgrade head; then
        echo "ERROR: alembic migration failed, refusing to start app" >&2
        exit 1
    fi
    rm /first_run
fi
if [ ! -z ${DEVELOPMENT+x} ]; then
    pdm sync -G dev
fi

# Uvicorn only honours X-Forwarded-* headers from IPs listed in
# FORWARDED_ALLOW_IPS. Traffic arriving via the published port is NATted to
# the compose network gateway, so trust that address (detected dynamically
# from the default route) unless an explicit override was provided.
if [ -z "$FORWARDED_ALLOW_IPS" ]; then
    FORWARDED_ALLOW_IPS=$(python - <<'EOF'
import socket, struct
gw = None
with open("/proc/net/route") as f:
    next(f)
    for line in f:
        parts = line.split()
        if parts[1] == "00000000":  # default route
            gw = int(parts[2], 16)
            break
print(socket.inet_ntoa(struct.pack("<I", gw)) if gw else "")
EOF
)
    [ -n "$FORWARDED_ALLOW_IPS" ] && export FORWARDED_ALLOW_IPS
fi

exec pdm run app
