#!/bin/sh
set -eu

database="${TDENGINE_DATABASE:-ashare}"
case "$database" in
    *[!A-Za-z0-9_]*|'')
        echo "Invalid TDENGINE_DATABASE identifier" >&2
        exit 1
        ;;
esac

endpoint="${TDENGINE_REST_URL:-http://tdengine:6041}/rest/sql"
user="${TDENGINE_USER:-root}"
password="${TDENGINE_PASSWORD:-taosdata}"

until curl --fail --silent --show-error --user "$user:$password" \
    --data-binary "SHOW DATABASES" "$endpoint" >/dev/null; do
    sleep 2
done

awk 'BEGIN { RS=";"; ORS="\n" } { gsub(/\n/, " "); print }' /schema.sql | sed "s/__DATABASE__/$database/g" |
while IFS= read -r statement; do
    compact="$(printf '%s' "$statement" | tr -d '[:space:]')"
    [ -z "$compact" ] && continue
    response="$(curl --fail --silent --show-error --user "$user:$password" \
        --data-binary "$statement" "$endpoint")"
    printf '%s' "$response" | grep -q '"code":0' || {
        echo "TDengine schema statement failed: $response" >&2
        exit 1
    }
done

echo "TDengine schema is ready: $database"
