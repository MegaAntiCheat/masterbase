mkdir -p "$EXPORT_DIR"

function execute () {
    psql -U $POSTGRES_USER -d $POSTGRES_NAME -h $POSTGRES_HOST -Atc "$1"
}

if [ $# -eq 0 ]; then
    TABLES=('reports' 'demo_sessions')
else
    TABLES=("$@")
fi

for TABLE in "${TABLES[@]}"; do
    COLUMNS=$(execute "
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = '$TABLE'
        AND data_type != 'bytea';
    ")

    execute "\COPY $TABLE TO STDOUT WITH CSV HEADER" \
        | gzip | mc pipe blobs/db_exports/$TABLE.csv.gz
done