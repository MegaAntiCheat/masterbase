FROM python:3.11-slim-buster

ARG DEVELOPMENT
ENV DEVELOPMENT=$DEVELOPMENT

WORKDIR /home

RUN pip install -q --upgrade --upgrade-strategy eager pip setuptools wheel && pip install pdm==2.10.1 && pip install pgcli

COPY . .

RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-utils \
    postgresql-client \
    dos2unix

RUN pdm sync --prod --no-editable

EXPOSE 8000

RUN touch /first_run

COPY services/api/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

RUN dos2unix /usr/local/bin/start.sh

ENTRYPOINT /usr/local/bin/start.sh
