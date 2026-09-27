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
exec pdm run app
