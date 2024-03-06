FROM python:3.11-slim-buster

WORKDIR /home

RUN pip install -q --upgrade --upgrade-strategy eager pip setuptools wheel && pip install pdm==2.10.1 && pip install pgcli

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    postgresql-client

RUN pdm sync --prod --no-editable

ENTRYPOINT pdm run uvicorn src.api.app:app --host 0.0.0.0 --timeout-keep-alive 20 --ws-ping-interval 10 --ws-ping-timeout 10
