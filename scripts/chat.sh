#!/usr/bin/env bash
# scripts/chat.sh <username> <question> [session-id]
set -euo pipefail

USER_NAME="${1:?usage: chat.sh <username> <question> [session-id]}"
QUESTION="${2:?usage: chat.sh <username> <question> [session-id]}"
SESSION="${3:-default}"

TOKEN=$(curl -s -X POST "http://localhost:8080/realms/acme/protocol/openid-connect/token" \
  -d "client_id=acme-chat" -d "grant_type=password" \
  -d "username=${USER_NAME}" -d "password=demo" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

BODY=$(Q="$QUESTION" S="$SESSION" python3 - <<'PY'
import json, os
print(json.dumps({"message": os.environ["Q"], "session_id": os.environ["S"]}))
PY
)

curl -s -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${BODY}" | python3 -m json.tool
