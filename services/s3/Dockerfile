FROM minio/minio:RELEASE.2024-06-06T09-36-42Z

VOLUME /blobs/

# Set environment variables
ENV MINIO_ROOT_USER=MEGASCATTERBOMB
ENV MINIO_ROOT_PASSWORD=masterbase

# Expose ports
EXPOSE 9000
EXPOSE 9001

COPY services/s3/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

ENTRYPOINT /usr/local/bin/start.sh