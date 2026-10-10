#!/bin/sh
# Lab 6: start a PostgreSQL hot standby that streams WAL from the primary.
#
# First start (empty data dir): wait for the primary, clone it with
# pg_basebackup, and let it replay from the replication slot. Later starts
# reuse the data dir and simply reconnect. Runs as root so it can fix
# ownership, then hands over to the image's own entrypoint (-> postgres user).
set -eu

PGDATA="${PGDATA:-/var/lib/postgresql/data}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "replica: empty data directory, cloning $PRIMARY_HOST with pg_basebackup"
    until pg_isready -h "$PRIMARY_HOST" -p 5432 >/dev/null 2>&1; do
        echo "replica: waiting for primary..."
        sleep 2
    done
    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    chmod 700 "$PGDATA"
    # -R writes standby.signal (+ primary_conninfo); -X stream ships WAL during
    # the copy so the clone is consistent when it finishes.
    PGPASSWORD="$REPLICATION_PASSWORD" su-exec postgres pg_basebackup \
        -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATION_USER" \
        -D "$PGDATA" -X stream -S "$REPLICATION_SLOT" -R -P
fi

# Command-line settings win over postgresql.auto.conf, so the connection (with
# its password) is always the current one regardless of how the clone was made.
exec docker-entrypoint.sh postgres \
    -c hot_standby=on \
    -c hot_standby_feedback=on \
    -c primary_conninfo="host=$PRIMARY_HOST port=5432 user=$REPLICATION_USER password=$REPLICATION_PASSWORD application_name=$REPLICATION_SLOT" \
    -c primary_slot_name="$REPLICATION_SLOT"
