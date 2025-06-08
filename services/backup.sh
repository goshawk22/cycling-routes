#!/bin/bash

set -e

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_NAME="backup_$TIMESTAMP.zip"

# Create a temporary directory for zipping
TMPDIR=$(mktemp -d)
cp -r uploads "$TMPDIR/"
cp routes.db "$TMPDIR/"

# Zip the contents
cd "$TMPDIR"
zip -r "$BACKUP_NAME" uploads routes.db
cd -

# Upload to Cloudflare R2 (S3-compatible)
# Requires AWS CLI configured for your R2 bucket
aws s3 cp "$TMPDIR/$BACKUP_NAME" s3://cycling-backup/backups/ --endpoint-url https://e309406756ae8f307fcafce3a31c6a88.r2.cloudflarestorage.com

echo "Backup completed and uploaded: $TMPDIR/$BACKUP_NAME"

# Cleanup
rm -rf "$TMPDIR"