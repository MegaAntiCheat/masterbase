mkdir -p "$EXPORT_DIR"

function execute () {
    psql -U $POSTGRES_USER -d $POSTGRES_NAME -h $POSTGRES_HOST -Atc "$1"
}

COLUMNS=$(execute <<-SQL
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = 'demo_sessions'
    AND data_type != 'bytea';
SQL
)
execute <<-SQL | gzip | mc pipe blobs/db_exports/demo_sessions.csv.gz
    \COPY $TABLE TO STDOUT WITH CSV HEADER
SQL
done