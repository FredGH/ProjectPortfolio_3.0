#!/usr/bin/env bash
# Register the daily Postgres backup as a cron job on the Oracle instance.
# Run once after initial deployment:
#   OCI_NAMESPACE=<your-namespace> OCI_BACKUP_BUCKET=<bucket> bash scripts/install_backup_cron.sh
#
# The cron job runs at 02:00 UTC daily and appends to /var/log/ca_backup.log.

set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/complaint_analyser}"
SCRIPT="$REMOTE_DIR/scripts/backup_postgres.sh"
LOG="/var/log/ca_backup.log"
OCI_NAMESPACE="${OCI_NAMESPACE:?OCI_NAMESPACE is required}"
OCI_BACKUP_BUCKET="${OCI_BACKUP_BUCKET:-complaint-analyser-backups}"

chmod +x "$SCRIPT"

CRON_LINE="0 2 * * * OCI_NAMESPACE=${OCI_NAMESPACE} OCI_BACKUP_BUCKET=${OCI_BACKUP_BUCKET} ${SCRIPT} >> ${LOG} 2>&1"

# Add only if not already present
( crontab -l 2>/dev/null | grep -qF "$SCRIPT" ) \
    && echo "Cron job already registered — skipping." \
    || ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -

echo "Cron job registered:"
crontab -l | grep "$SCRIPT"
echo ""
echo "Backup log: $LOG"
echo "Bucket: $OCI_BACKUP_BUCKET (namespace: $OCI_NAMESPACE)"
