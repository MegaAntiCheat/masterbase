#!/bin/bash

if [ ! -f .env ]; then
    cp .env.example .env
fi

# Validate ANALYSIS_BINARY
ANALYSIS_BINARY=$(grep '^ANALYSIS_BINARY=' .env | cut -d'=' -f2-)
if [ ! -f "$ANALYSIS_BINARY" ]; then
    echo "ERROR: ANALYSIS_BINARY not found at '$ANALYSIS_BINARY'"
    echo "Please set ANALYSIS_BINARY in .env to the path of the analysis executable."
    exit 1
fi

docker compose down

docker compose up -d --build api db minio
