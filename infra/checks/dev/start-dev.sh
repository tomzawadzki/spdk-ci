#!/bin/bash
# Start the SPDK Checks development environment.
set -e
cd "$(dirname "$0")"

echo "Starting SPDK Checks development environment..."
echo "This will start Gerrit + checks-api + nginx on http://localhost:8080"
echo ""

# Build and start services
docker compose -f docker-compose.dev.yaml up -d --build

echo ""
echo "Waiting for services to be ready..."

# Wait for checks-api (proxied through nginx)
for i in $(seq 1 60); do
    if curl -sf http://localhost:8080/checks-api/v1/health > /dev/null 2>&1; then
        echo "checks-api is ready!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "WARNING: checks-api did not become healthy in time."
        echo "Check logs: docker compose -f docker-compose.dev.yaml logs checks-api"
    fi
    sleep 2
done

# Optionally set up Gerrit and seed data
if [[ "${1:-}" == "--seed" ]]; then
    echo ""
    echo "Setting up Gerrit test project..."
    ./setup-gerrit.sh

    echo ""
    echo "Seeding test data..."
    ./seed-test-data.sh
fi

echo ""
echo "=== Development environment is ready ==="
echo ""
echo "  Gerrit UI:   http://localhost:8080"
echo "  Checks API:  http://localhost:8080/checks-api/v1/health"
echo ""
echo "Quick start:"
echo "  1. Open http://localhost:8080 in your browser"
echo "  2. Run ./setup-gerrit.sh to create a test project"
echo "  3. Run ./seed-test-data.sh to populate CI check data"
echo "  4. Navigate to a change and click the 'Checks' tab"
echo ""
echo "Or run with --seed to do steps 2-3 automatically:"
echo "  ./start-dev.sh --seed"
echo ""
echo "To stop: ./stop-dev.sh"
