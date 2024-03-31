export STEAM_API_KEY=no
export POSTGRES_USER=MEGASCATTERBOMB
export POSTGRES_PASSWORD=masterbase
export POSTGRES_HOST=172.20.1.10
export POSTGRES_PORT=5432
export API_HOST=172.20.1.20

echo $1

if [[ "$1" == "--replace" ]]; then
    echo "Replacing containers..."
    docker stop masterbase-db-dev
    docker stop masterbase-api-dev
    docker rm masterbase-api-dev
    docker rmi masterbase-api-dev
    docker network rm masterbase-network-dev
fi

pdm sync -G:all
docker network create --driver bridge masterbase-network-dev --subnet=172.20.0.0/16
docker build -f Dockerfile.db . -t db-dev
docker create --name masterbase-db-dev --network masterbase-network-dev --ip $POSTGRES_HOST -p 8050:5432 -e POSTGRES_USER=$POSTGRES_USER -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD -e POSTGRES_DB=demos -t db-dev
docker start masterbase-db-dev
docker build -f Dockerfile.api . -t api-dev
docker create --name masterbase-api-dev --network masterbase-network-dev --ip $API_HOST -p 8000:8000 -e POSTGRES_USER=$POSTGRES_USER -e POSTGRES_PASSWORD=$POSTGRES_PASSWORD -e POSTGRES_HOST=$POSTGRES_HOST -e POSTGRES_PORT=$POSTGRES_PORT -t api-dev
docker start masterbase-api-dev