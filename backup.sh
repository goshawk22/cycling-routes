#!/bin/bash

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <destination_folder>"
    exit 1
fi

DEST="$1"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="$DEST/$TIMESTAMP"

mkdir -p "$BACKUP_DIR"
cp -r uploads "$BACKUP_DIR/"
cp routes.db "$BACKUP_DIR/"

echo "Backup completed: $BACKUP_DIR"