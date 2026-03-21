#!/bin/bash
cd "$(dirname "$0")"
docker compose -f docker-compose.dev.yaml down
echo "Development environment stopped."
