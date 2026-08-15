#!/bin/bash

if [ ! -f vars.env ]; then
    cp vars.env.example vars.env
fi

docker-compose stop api db minio
docker-compose rm -f api db minio
docker rmi masterbase-api --force

docker-compose up -d --build api db minio

docker update --restart always api db minio
