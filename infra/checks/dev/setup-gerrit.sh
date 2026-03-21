#!/bin/bash
# Wait for Gerrit to be ready and create a test project.
#
# Gerrit ships with admin/secret credentials in dev mode.
# Changes must be created via git push (see README instructions at the end).
set -euo pipefail

DEV_PORT="${DEV_PORT:-9080}"
GERRIT_URL="http://localhost:$DEV_PORT"

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

# ---- Configure Verified label -----------------------------------------------
echo "Configuring Verified label..."
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$GERRIT_URL/a/projects/All-Projects/labels/Verified" \
    -H 'Content-Type: application/json' \
    --user admin:secret \
    -d '{
        "commit_message": "Add Verified label",
        "values": {" 0":"No score","-1":"Fails","+1":"Verified"},
        "default_value": 0,
        "function": "NoBlock",
        "copy_conditions": "changekind:NO_CHANGE"
    }')
case "$status" in
    200|201) echo "Verified label created." ;;
    409)     echo "Verified label already exists (OK)." ;;
    *)       echo "WARNING: Unexpected status $status creating Verified label." ;;
esac

# Grant Verified -1..+1 permission to Administrators on refs/heads/*
echo "Granting Verified label permission..."
ADMIN_UUID=$(curl -sf "$GERRIT_URL/a/groups/Administrators" --user admin:secret \
    | sed 's/^)]}.//' | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -sf -X POST "$GERRIT_URL/a/projects/All-Projects/access" \
    --user admin:secret \
    -H 'Content-Type: application/json' \
    -d "{
        \"add\": {
            \"refs/heads/*\": {
                \"permissions\": {
                    \"label-Verified\": {
                        \"rules\": {
                            \"$ADMIN_UUID\": {
                                \"action\": \"ALLOW\",
                                \"min\": -1,
                                \"max\": 1
                            }
                        }
                    }
                }
            }
        }
    }" > /dev/null
echo "Verified permission granted."

echo ""
echo "=== Gerrit setup complete ==="
echo ""
echo "To create a test change, clone and push:"
echo "  git clone http://admin:secret@localhost:$DEV_PORT/a/test-project"
echo "  cd test-project"
echo "  echo 'hello' > test.txt && git add . && git commit -m 'Test change'"
echo "  git push origin HEAD:refs/for/master"
