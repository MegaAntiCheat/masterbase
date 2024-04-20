setlocal

:: Set environment variables
set "STEAM_API_KEY=no"
set "POSTGRES_USER=admin"
set "POSTGRES_PASSWORD=admin"
set "POSTGRES_HOST=172.20.1.10"
set "POSTGRES_PORT=5432"
set "API_HOST=172.20.1.20"
set "SUBNET=172.20.0.0/16"

:: Check for "--replace" argument
if "%~1"=="--replace" (
    echo Replacing containers...
    docker stop masterbase-db-dev
    docker stop masterbase-api-dev
    docker rm masterbase-api-dev
    docker rmi api-dev
    docker rm masterbase-db-dev
    docker rmi db-dev
    docker network rm masterbase-network-dev
)

:: Check for "--development" argument
if "%~1"=="--development" (
    echo Running in development mode...
    set "DEVELOPMENT=true"
)

pdm sync -G:all
docker network create --driver bridge masterbase-network-dev --subnet=%SUBNET%
docker build -f Dockerfile.db . -t db-dev
docker create --name masterbase-db-dev --network masterbase-network-dev --ip %POSTGRES_HOST% -p 8050:%POSTGRES_PORT% -e POSTGRES_USER=%POSTGRES_USER% -e POSTGRES_PASSWORD=%POSTGRES_PASSWORD% -e POSTGRES_DB=demos -t db-dev
docker start masterbase-db-dev
docker build -f Dockerfile.api . --build-arg DEVELOPMENT=%DEVELOPMENT% -t api-dev
docker create --name masterbase-api-dev --network masterbase-network-dev --ip %API_HOST% -p 8000:8000 -e POSTGRES_USER=%POSTGRES_USER% -e POSTGRES_PASSWORD=%POSTGRES_PASSWORD% -e POSTGRES_HOST=%POSTGRES_HOST% -e POSTGRES_PORT=%POSTGRES_PORT% -t api-dev
docker start masterbase-api-dev
