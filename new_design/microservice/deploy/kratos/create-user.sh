#!/usr/bin/env bash
# Создаёт сотрудника контакт-центра в Ory Kratos через Admin API (порт 4434).
# Использование:
#   ./create-user.sh <email> <password> "<ФИО>" <Оператор|Администратор>
set -euo pipefail

EMAIL="${1:?email}"
PASSWORD="${2:?password}"
NAME="${3:-Сотрудник}"
ROLE="${4:-Оператор}"
ADMIN_URL="${KRATOS_ADMIN_URL:-http://localhost:4434}"

curl -sS -X POST "$ADMIN_URL/admin/identities" \
  -H 'Content-Type: application/json' \
  -d "$(cat <<JSON
{
  "schema_id": "employee",
  "traits": { "email": "$EMAIL", "name": "$NAME", "role": "$ROLE" },
  "credentials": { "password": { "config": { "password": "$PASSWORD" } } }
}
JSON
)"
echo
echo "Готово: $EMAIL ($ROLE)"
