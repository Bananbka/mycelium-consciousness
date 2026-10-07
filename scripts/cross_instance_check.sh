#!/usr/bin/env bash
# Lab 2: cross-instance consistency without sticky sessions.
# Usage: scripts/cross_instance_check.sh [base_url]   (default http://localhost)
set -euo pipefail
BASE="${1:-http://localhost}"
EMAIL="xi-$(date +%s)@example.com"
PASS="crossinst-pass-123"
hdr() { grep -i '^x-instance-id' | tr -d '\r'; }

curl -s -X POST "$BASE/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"designation\":\"xi-$$\"}" >/dev/null
TOKEN=$(curl -s -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"

echo "== GET profile (nginx picks an instance)"
curl -si "$BASE/profiles/me" -H "$AUTH" | tee /tmp/xi1 | hdr
ID=$(tail -1 /tmp/xi1 | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

for status in "deceased" "active" "deceased"; do
  echo "== PATCH status=$status"
  curl -si -X PATCH "$BASE/profiles/$ID" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"status\":\"$status\"}" | hdr
  echo "== GET profile -> expect status=$status"
  curl -si "$BASE/profiles/me" -H "$AUTH" | tee /tmp/xi2 | hdr
  tail -1 /tmp/xi2 | python -c 'import sys,json;print("   status:",json.load(sys.stdin)["status"])'
done
