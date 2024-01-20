FROM python:3.11-slim-buster

WORKDIR /home

RUN pip install -q --upgrade --upgrade-strategy eager pip setuptools wheel && pip install pdm && pip install pgcli

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    postgresql-client

RUN pdm sync --prod --no-editable

ENTRYPOINT pdm run litestar --app-dir src/api run --host 0.0.0.0
