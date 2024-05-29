
FROM python:3.11-slim-buster

ARG DEVELOPMENT
ENV DEVELOPMENT=$DEVELOPMENT

WORKDIR /home

RUN pip install -q --upgrade --upgrade-strategy eager pip setuptools wheel && pip install pdm==2.10.1 && pip install pgcli

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    postgresql-client

RUN pdm sync --prod --no-editable

EXPOSE 8000

RUN touch /first_run
ENTRYPOINT ["/bin/sh", "-c", "if [ -f /first_run ]; then pdm run alembic upgrade head; rm /first_run; fi; exec pdm run app"]
