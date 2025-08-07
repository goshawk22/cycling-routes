#!/bin/bash

# Maintenance mode toggle script
# Usage: ./maintenance.sh enable|disable

set -e

# Source environment variables
if [ -f .env ]; then
    source .env
else
    echo "Error: .env file not found"
    exit 1
fi

# Validate required environment variables
if [ -z "$CLOUDFLARE_API_TOKEN" ] || [ -z "$CLOUDFLARE_ZONE_ID" ] || [ -z "$CLOUDFLARE_ROUTE_ID" ]; then
    echo "Error: Missing required environment variables (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID, CLOUDFLARE_ROUTE_ID)"
    exit 1
fi

# Check argument
if [ $# -ne 1 ] || [[ ! "$1" =~ ^(enable|disable)$ ]]; then
    echo "Usage: $0 {enable|disable}"
    exit 1
fi

MODE="$1"
BASE_URL="https://api.cloudflare.com/client/v4/zones/$CLOUDFLARE_ZONE_ID/workers/routes/$CLOUDFLARE_ROUTE_ID"

case "$MODE" in
    enable)
        echo "Enabling maintenance mode..."
        curl --location --request PUT "$BASE_URL" \
             -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
             -H "Content-Type: application/json" \
             -d '{"pattern":"routes.225050.xyz","script":"maintenance-page"}'
        ;;
    disable)
        echo "Disabling maintenance mode..."
        curl --location --request PUT "$BASE_URL" \
             -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
             -H "Content-Type: application/json" \
             -d '{"pattern":"maintenance.225050.xyz","script":"maintenance-page"}'
        ;;
esac

echo "Maintenance mode ${MODE}d successfully"