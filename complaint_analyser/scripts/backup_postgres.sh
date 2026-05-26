#!/usr/bin/env bash
# Daily Postgres backup to OCI Object Storage (S3-compatible).
#
# Prerequisites on the Oracle instance:
#   1. oci CLI installed and configured:  oci setup config
#   2. An OCI Object Storage bucket created (set OCI_BACKUP_BUCKET below or in env)
#   3. This script registered as a daily cron (see scripts/install_backup_cron.sh)
#
# Required env vars (set in /etc/environment or the cron job):
#   OCI_NAMESPACE   — OCI Object Storage namespace (visible in Console → Tenancy)
#   OCI_BACKUP_BUCKET — Bucket name (default: complaint-analyser-backups)
#
# Retention: backups older than RETENTION_DAYS are pruned from the bucket.

set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/complaint_analyser}"
COMPOSE_FILE="$REMOTE_DIR/docker-compose.yml"
PROD_FILE="$REMOTE_DIR/docker-compose.prod.yml"
BACKUP_DIR="/tmp/ca_backups"
BUCKET="${OCI_BACKUP_BUCKET:-complaint-analyser-backups}"
NAMESPACE="${OCI_NAMESPACE:?OCI_NAMESPACE environment variable is required}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="triage_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%FT%TZ)] Starting Postgres backup → $FILENAME"

docker compose -f "$COMPOSE_FILE" -f "$PROD_FILE" exec -T postgres \
    pg_dump -U triage triage | gzip > "$BACKUP_DIR/$FILENAME"

FILESIZE=$(du -sh "$BACKUP_DIR/$FILENAME" | cut -f1)
echo "[$(date -u +%FT%TZ)] Dump complete ($FILESIZE). Uploading to OCI bucket '$BUCKET'..."

oci os object put \
    --namespace "$NAMESPACE" \
    --bucket-name "$BUCKET" \
    --name "postgres/$FILENAME" \
    --file "$BACKUP_DIR/$FILENAME" \
    --force \
    --no-multipart

rm -f "$BACKUP_DIR/$FILENAME"
echo "[$(date -u +%FT%TZ)] Upload complete. Local file removed."

# Prune objects older than RETENTION_DAYS.
# OCI CLI returns time-created in ISO-8601; we compare the date prefix of the filename.
CUTOFF_PREFIX=$(date -u -d "-${RETENTION_DAYS} days" +%Y%m%d 2>/dev/null \
    || date -u -v-${RETENTION_DAYS}d +%Y%m%d)

echo "[$(date -u +%FT%TZ)] Pruning backups with date prefix < $CUTOFF_PREFIX..."

oci os object list \
    --namespace "$NAMESPACE" \
    --bucket-name "$BUCKET" \
    --prefix "postgres/triage_" \
    --query "data[].name" \
    --output json 2>/dev/null \
| jq -r '.[]' \
| while read -r obj; do
    # Extract date portion: postgres/triage_YYYYMMDD_HHMMSS.sql.gz
    obj_date=$(basename "$obj" | grep -oE '[0-9]{8}' | head -1 || true)
    if [[ -n "$obj_date" && "$obj_date" < "$CUTOFF_PREFIX" ]]; then
        oci os object delete \
            --namespace "$NAMESPACE" \
            --bucket-name "$BUCKET" \
            --object-name "$obj" \
            --force
        echo "[$(date -u +%FT%TZ)] Deleted old backup: $obj"
    fi
done

echo "[$(date -u +%FT%TZ)] Backup job complete."
