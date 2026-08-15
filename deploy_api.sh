#!/bin/bash

if [ ! -f vars.env ]; then
    cp vars.env.example vars.env
fi

docker-compose stop api
docker-compose rm -f api
docker rmi masterbase-api --force
docker-compose up -d --build api
