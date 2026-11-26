#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -f .env.local ]]; then
  echo "请先复制 .env.local.example 为 .env.local 并按需调整配置。" >&2
  exit 1
fi

set -a
source .env.local
set +a

mkdir -p "${DATA_ROOT}/files"
.venv/bin/python manage.py migrate
exec .venv/bin/python manage.py runserver 127.0.0.1:8000
