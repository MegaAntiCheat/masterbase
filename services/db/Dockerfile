FROM minio/mc:RELEASE.2024-06-10T16-44-15Z AS mc
FROM postgres:16.1-bullseye

VOLUME /var/lib/postgresql/data

EXPOSE 5432

RUN mc alias set blobs "http://$MINIO_HOST:$MINIO_PORT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

COPY ./services/db/export_db.sh /usr/local/bin/export_db.sh
RUN chmod +x /usr/local/bin/export_db.sh

RUN apt-get update && apt-get install -y cron
RUN mkdir -p /etc/cron.d
COPY ./services/db/export_db.cron /etc/cron.d/export_db.cron
RUN chmod 0644 /etc/cron.d/export_db.cron
RUN crontab /etc/cron.d/export_db.cron
RUN service cron start