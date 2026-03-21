#!/bin/bash
# Wait for Gerrit to be ready and create a test project.
#
# Gerrit ships with admin/secret credentials in dev mode.
# Changes must be created via git push (see README instructions at the end).
set -euo pipefail

GERRIT_URL="http://localhost:8080"

echo "Waiting for Gerrit to be ready..."
for i in $(seq 1 90); do
    if curl -sf "$GERRIT_URL/config/server/version" | grep -q "3\."; then
        echo "Gerrit is ready!"
        break
    fi
    if [ "$i" -eq 90 ]; then
        echo "ERROR: Gerrit did not start in time." >&2
        exit 1
    fi
    sleep 2
done

# Create test project. Gerrit may return 409 if it already exists — that's fine.
echo "Creating test project 'test-project'..."
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$GERRIT_URL/a/projects/test-project" \
    -H 'Content-Type: application/json' \
    --user admin:secret \
    -d '{"description":"Test project for checks plugin development","create_empty_commit":true}')

case "$status" in
    200|201) echo "Project created." ;;
    409)     echo "Project already exists (OK)." ;;
    *)       echo "WARNING: Unexpected status $status creating project." ;;
esac

echo ""
echo "=== Gerrit setup complete ==="
echo ""
echo "To create a test change, clone and push:"
echo "  git clone http://admin:secret@localhost:8080/a/test-project"
echo "  cd test-project"
echo "  echo 'hello' > test.txt && git add . && git commit -m 'Test change'"
echo "  git push origin HEAD:refs/for/master"
