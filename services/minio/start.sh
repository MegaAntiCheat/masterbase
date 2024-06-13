minio server /blobs --console-address :9001 &
S3_PID=$!

until curl --output /dev/null --silent --head --fail http://localhost:9000; do
  sleep 1
done

if [ -f /first_run ]; then
    mc alias set blobs http://localhost:9000 MEGASCATTERBOMB masterbase
    mc mb -p blobs/demos
    rm /first_run
fi

wait S3_PID