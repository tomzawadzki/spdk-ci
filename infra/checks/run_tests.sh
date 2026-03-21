#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "=== Backend tests ==="
python3 -m pytest tests/ -v

echo ""
echo "=== Frontend tests ==="
cd frontend && node test.mjs

echo ""
echo "All tests passed!"
