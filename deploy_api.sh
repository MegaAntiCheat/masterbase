#!/bin/bash

if [ ! -f vars.env ]; then
    cp vars.env.example vars.env
fi

export COMPOSE_PROJECT_NAME=$(grep '^COMPOSE_PROJECT_NAME=' vars.env | cut -d'=' -f2-)

# Validate ANALYSIS_BINARY
ANALYSIS_BINARY=$(grep '^ANALYSIS_BINARY=' vars.env | cut -d'=' -f2-)
if [ ! -f "$ANALYSIS_BINARY" ]; then
    echo "ERROR: ANALYSIS_BINARY not found at '$ANALYSIS_BINARY'"
    echo "Please set ANALYSIS_BINARY in vars.env to the path of the analysis executable."
    exit 1
fi

docker-compose --env-file vars.env down --rmi local

docker-compose --env-file vars.env up -d --build api
