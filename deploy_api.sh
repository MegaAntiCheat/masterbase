docker-compose stop apis
docker-compose rm -f api
docker rmi masterbase-api --force
docker-compose up -d api
