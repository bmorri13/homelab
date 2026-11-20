#!/usr/bin/env bash
# Check a PostgreSQL image bump for Authentik's database before it merges.
#   postgres-upgrade-test.sh <values.yaml before> <values.yaml after>
# Does what the cluster does on a merge, on a throwaway volume: starts the old image,
# writes data, stops it, and starts the new image on the same data directory. Passes when
# the new image starts, reports the version in its tag, reads the old data back unchanged,
# uses the old index, takes writes and can run pg_dump (the nightly backup uses this image).
# Fails on a change of major version: that needs a dump and restore by hand.
# No secrets and no Authentik data. Run by .github/workflows/postgres-upgrade.yml, or by
# hand on any machine with Docker; it removes its container and volume on exit.
set -euo pipefail

[[ $# -eq 2 ]] || { echo "usage: $0 <values.yaml before> <values.yaml after>" >&2; exit 2; }

image_of() {
    local found
    found=$(awk '$1 == "image:" && $2 ~ /\/postgres:/ {print $2}' "$1" | sort -u)
    [[ -n "$found" && "$found" != *$'\n'* ]] || { echo "postgres: expected one postgres image in $1, found: ${found:-none}" >&2; exit 1; }
    printf '%s' "$found"
}
version_of() { local v=${1##*/postgres:}; v=${v%%@*}; printf '%s' "${v%%-*}"; }

old=$(image_of "$1"); new=$(image_of "$2")
old_version=$(version_of "$old"); new_version=$(version_of "$new")
echo "postgres: $old_version -> $new_version"
if [[ "${old_version%%.*}" != "${new_version%%.*}" ]]; then
    echo "postgres: a new major version cannot open the old data directory. Move it by hand with a dump and restore." >&2
    exit 1
fi

name="pg-ci-$$"; volume="pg-ci-$$"
password=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')  # for this test database only
ok=0
cleanup() {
    [[ $ok -eq 1 ]] || docker logs --tail 40 "$name" 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The same database, user and data directory as templates/postgres.yaml.
start() {
    docker run -d --name "$name" -e POSTGRES_DB=authentik -e POSTGRES_USER=authentik -e "POSTGRES_PASSWORD=$password" \
        -e PGDATA=/var/lib/postgresql/data/pgdata -v "$volume:/var/lib/postgresql/data" "$1" >/dev/null
    # Over TCP: during first start the image runs a temporary server on the socket only.
    for _ in $(seq 1 60); do
        docker exec "$name" pg_isready -q -h 127.0.0.1 -U authentik -d authentik && return 0
        sleep 2
    done
    echo "postgres: $1 did not become ready" >&2
    return 1
}
sql() { docker exec -e "PGPASSWORD=$password" "$name" psql -h 127.0.0.1 -U authentik -d authentik -v ON_ERROR_STOP=1 -AtqX -c "$1"; }
fingerprint() { sql "SELECT count(*) || ':' || md5(string_agg(id || note || doc::text, ',' ORDER BY id)) FROM upgrade_test"; }

start "$old"
sql "CREATE TABLE upgrade_test (id int PRIMARY KEY, note text NOT NULL, doc jsonb NOT NULL);
     INSERT INTO upgrade_test SELECT n, md5(n::text), jsonb_build_object('n', n, 'even', n % 2 = 0) FROM generate_series(1, 5000) n;
     CREATE INDEX upgrade_test_doc ON upgrade_test USING gin (doc);
     CREATE INDEX upgrade_test_note ON upgrade_test (note);"
before=$(fingerprint)
docker stop -t 60 "$name" >/dev/null   # a clean shutdown, as on a pod restart
docker rm "$name" >/dev/null
echo "postgres: wrote $before on $old_version"

start "$new"
running=$(sql "SHOW server_version")
[[ "$running" == "$new_version"* ]] || { echo "postgres: the image reports $running, expected $new_version" >&2; exit 1; }
after=$(fingerprint)
[[ "$after" == "$before" ]] || { echo "postgres: data changed across the restart: $before -> $after" >&2; exit 1; }
found=$(sql "SET enable_seqscan = off; SELECT count(*) FROM upgrade_test WHERE note = md5('4242')")
[[ "$found" == 1 ]] || { echo "postgres: the old index returned $found rows, expected 1" >&2; exit 1; }
sql "INSERT INTO upgrade_test VALUES (5001, 'new', '{}'); UPDATE upgrade_test SET note = 'changed' WHERE id = 1; REINDEX TABLE upgrade_test;"
docker exec -e "PGPASSWORD=$password" "$name" pg_dump -h 127.0.0.1 -U authentik -d authentik >/dev/null
ok=1
echo "postgres: $new_version started on $old_version data, read it back unchanged, took writes and ran pg_dump"
