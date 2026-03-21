#!/bin/bash
set -e
cd "$(dirname "$0")"

# Ensure infra/ is on PYTHONPATH for the common package
INFRA_DIR="$(cd .. && pwd)"
export PYTHONPATH="$INFRA_DIR:$(pwd):${PYTHONPATH:-}"

echo "=== Common module tests ==="
python3 -m pytest "$INFRA_DIR/common/tests/" -v

echo ""
echo "=== Backend unit tests ==="
python3 -m pytest tests/ -v --ignore=tests/test_functional.py

echo ""
echo "=== Backend functional tests ==="
python3 -m pytest tests/test_functional.py -v

echo ""
echo "=== Frontend tests ==="
cd frontend && node test.mjs

echo ""
echo "All tests passed!"
