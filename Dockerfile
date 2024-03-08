FROM python:3.11-slim-buster

WORKDIR /home

RUN pip install -q --upgrade --upgrade-strategy eager pip setuptools wheel && pip install pdm==2.10.1 && pip install pgcli

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    postgresql-client

RUN pdm sync --prod --no-editable

ENTRYPOINT pdm run app
