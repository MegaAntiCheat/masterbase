FROM chainguard/minio:latest

# Image ships with a non-root default user; we need root to install the start
# script and to write to the /blobs volume at runtime.
# The minimal base image has no /usr/local/bin, so create it.
USER root
RUN mkdir -p /usr/local/bin

VOLUME /blobs/

# Set environment variables
ENV MINIO_ROOT_USER=MEGASCATTERBOMB
ENV MINIO_ROOT_PASSWORD=masterbase

# Expose ports
EXPOSE 9000
EXPOSE 9001


COPY services/minio/start.sh /usr/local/bin/start.sh
RUN tr -d '\r' < /usr/local/bin/start.sh > /usr/local/bin/start_unix.sh && mv /usr/local/bin/start_unix.sh /usr/local/bin/start.sh

RUN chmod +x /usr/local/bin/start.sh

ENTRYPOINT /usr/local/bin/start.sh