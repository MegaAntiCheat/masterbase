setlocal

:: Set environment variables
set "STEAM_API_KEY=no"
set "POSTGRES_USER=admin"
set "POSTGRES_PASSWORD=admin"
set "POSTGRES_HOST=172.20.1.10"
set "POSTGRES_PORT=5432"
set "API_HOST=172.20.1.20"

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

:: Run other commands (pdm sync, docker network, etc.)
:: Note: You'll need to adapt these commands to Windows equivalents.
pdm sync -G:all
docker start masterbase-api-dev
