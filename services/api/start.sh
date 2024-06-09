#!/bin/sh
# run alembic here because we are not forwarding the DB
if [ -f /first_run ]; then
    pdm run alembic upgrade head
    rm /first_run
fi
if [ ! -z ${DEVELOPMENT+x}]; then
    pdm sync -G dev
fi
exec pdm run app
