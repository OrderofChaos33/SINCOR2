#!/bin/bash

# SINCOR Automated Backup Script
# Run via cron: 0 2 * * * /opt/sincor/deploy/backup.sh

set -e

# Configuration
BACKUP_DIR="/opt/sincor-backups"
APP_DIR="/opt/sincor"
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="sincor_backup_$DATE"

# Create backup directory
mkdir -p $BACKUP_DIR

echo "🗄️  Starting SINCOR backup: $BACKUP_NAME"

# Create backup archive
cd $APP_DIR
tar -czf "$BACKUP_DIR/$BACKUP_NAME.tar.gz" \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='logs/*.log' \
    .

# Backup databases (if using external DB)
if command -v pg_dump &> /dev/null; then
    echo "📊 Backing up PostgreSQL..."
    pg_dump sincor > "$BACKUP_DIR/${BACKUP_NAME}_db.sql"
fi

# Backup logs separately (compressed)
echo "📝 Backing up logs..."
tar -czf "$BACKUP_DIR/${BACKUP_NAME}_logs.tar.gz" logs/

# Upload to cloud storage (configure your preferred provider)
# Example for DigitalOcean Spaces:
# s3cmd put "$BACKUP_DIR/$BACKUP_NAME.tar.gz" s3://your-backup-bucket/sincor/

# Example for AWS S3:
# aws s3 cp "$BACKUP_DIR/$BACKUP_NAME.tar.gz" s3://your-backup-bucket/sincor/

# Clean old backups
echo "🧹 Cleaning old backups (older than $RETENTION_DAYS days)..."
find $BACKUP_DIR -name "sincor_backup_*" -type f -mtime +$RETENTION_DAYS -delete

# Health check - ensure backup was created
if [ -f "$BACKUP_DIR/$BACKUP_NAME.tar.gz" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_DIR/$BACKUP_NAME.tar.gz" | cut -f1)
    echo "✅ Backup completed successfully: $BACKUP_SIZE"
    
    # Send notification (optional)
    # curl -X POST -H 'Content-type: application/json' \
    #     --data '{"text":"SINCOR backup completed: '$BACKUP_NAME' ('$BACKUP_SIZE')"}' \
    #     YOUR_SLACK_WEBHOOK_URL
else
    echo "❌ Backup failed!"
    exit 1
fi

echo "📋 Backup summary:"
echo "  - Archive: $BACKUP_NAME.tar.gz"
echo "  - Size: $BACKUP_SIZE"
echo "  - Location: $BACKUP_DIR"
echo "  - Retention: $RETENTION_DAYS days"