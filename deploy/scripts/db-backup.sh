#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-backup.sh [--env-file PATH] [--output-dir DIR] [--retention-days DAYS]

Creates a PostgreSQL custom-format dump with pg_dump.
DATABASE_URL may be provided by the environment or by --env-file.
When --retention-days is set, old cwwd-*.dump and cwwd-*.dump.sha256 files are pruned after a successful backup.
USAGE
}

load_env_file() {
  local file="$1"
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case "$key" in
      DATABASE_URL|DATABASE_URL_DIRECT|BACKUP_DIR|BACKUP_RETENTION_DAYS|PG_DUMP_BIN)
        printf -v "$key" '%s' "$value"
        ;;
    esac
  done < "$file"
}

normalize_database_url() {
  local url="$1"
  if [[ "$url" == postgresql+*://* ]]; then
    url="postgresql://${url#*://}"
  fi
  printf '%s' "$url"
}

redact_pg_stderr() {
  sed -E 's#postgresql(\\+[^:]+)?://[^@[:space:]]+@#postgresql://***:***@#g' >&2
}

default_pg_dump_bin() {
  local candidates=()
  local path
  for path in /usr/lib/postgresql/*/bin/pg_dump; do
    [[ -x "$path" ]] && candidates+=("$path")
  done
  if [[ ${#candidates[@]} -gt 0 ]]; then
    printf '%s\n' "${candidates[@]}" | sort -V | tail -n 1
  else
    printf '%s\n' pg_dump
  fi
}

validate_retention_days() {
  local days="$1"
  if [[ ! "$days" =~ ^[0-9]+$ || "$days" == "0" ]]; then
    echo "--retention-days must be a positive integer" >&2
    exit 2
  fi
}

prune_old_backups() {
  local dir="$1"
  local days="$2"
  local files=()
  local file
  mapfile -t files < <(
    find "$dir" -maxdepth 1 -type f \( -name 'cwwd-*.dump' -o -name 'cwwd-*.dump.sha256' \) -mtime +"$days" -print
  )
  for file in "${files[@]}"; do
    rm -f -- "$file"
  done
  echo "retention_deleted=${#files[@]}"
}

TMP_PGPASSFILE=""
configure_libpq_env_from_url() {
  local url="$1"
  TMP_PGPASSFILE="$(mktemp)"
  chmod 600 "$TMP_PGPASSFILE"
  eval "$(
    CWWD_DB_URL="$url" CWWD_PGPASSFILE="$TMP_PGPASSFILE" python3 - <<'PY'
import os
import shlex
from urllib.parse import parse_qs, unquote, urlparse

url = os.environ["CWWD_DB_URL"]
passfile = os.environ["CWWD_PGPASSFILE"]
parsed = urlparse(url)
query = parse_qs(parsed.query, keep_blank_values=True)

host = parsed.hostname or ""
port = str(parsed.port) if parsed.port else ""
database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")

def pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")

if password:
    with open(passfile, "w", encoding="utf-8") as handle:
        handle.write(
            ":".join(
                pgpass_escape(part or "*")
                for part in (host, port, database, user, password)
            )
            + "\n"
        )
else:
    open(passfile, "w", encoding="utf-8").close()

def emit(name: str, value: str) -> None:
    if value:
        print(f"export {name}={shlex.quote(value)}")

emit("PGHOST", host)
emit("PGPORT", port)
emit("PGDATABASE", database)
emit("PGUSER", user)
emit("PGSSLMODE", query.get("sslmode", [""])[0])
emit("PGCHANNELBINDING", query.get("channel_binding", [""])[0])
print(f"export PGPASSFILE={shlex.quote(passfile)}")
PY
  )"
  unset DATABASE_URL DATABASE_URL_DIRECT CWWD_DB_URL CWWD_PGPASSFILE
}

ENV_FILE=""
OUTPUT_DIR="${BACKUP_DIR:-backups/postgres}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:?--output-dir requires a path}"
      shift 2
      ;;
    --retention-days)
      RETENTION_DAYS="${2:?--retention-days requires a positive integer}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Env file not found: $ENV_FILE" >&2
    exit 2
  fi
  unset DATABASE_URL DATABASE_URL_DIRECT
  load_env_file "$ENV_FILE"
fi

DB_URL="$(normalize_database_url "${DATABASE_URL_DIRECT:-${DATABASE_URL:-}}")"

if [[ -z "$DB_URL" ]]; then
  echo "DATABASE_URL_DIRECT or DATABASE_URL is required" >&2
  exit 2
fi

if [[ "$DB_URL" != postgresql* ]]; then
  echo "DATABASE_URL must be PostgreSQL for backup" >&2
  exit 2
fi

if [[ "$DB_URL" == *"-pooler."* ]]; then
  echo "Use DATABASE_URL_DIRECT with a direct/unpooled Neon URL for pg_dump, not a pooled -pooler URL" >&2
  exit 2
fi

unset DATABASE_URL DATABASE_URL_DIRECT

if [[ -n "$RETENTION_DAYS" ]]; then
  validate_retention_days "$RETENTION_DAYS"
fi

PG_DUMP_BIN="${PG_DUMP_BIN:-$(default_pg_dump_bin)}"

command -v "$PG_DUMP_BIN" >/dev/null 2>&1 || {
  echo "pg_dump is required but was not found in PATH" >&2
  exit 127
}

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUTPUT_DIR}/cwwd-${STAMP}.dump"
MANIFEST="${OUT}.sha256"
TMP_OUT="${OUT}.tmp"

cleanup() {
  rm -f "$TMP_OUT"
  if [[ -n "$TMP_PGPASSFILE" ]]; then
    rm -f "$TMP_PGPASSFILE"
  fi
}
trap cleanup EXIT

rm -f "$TMP_OUT"
configure_libpq_env_from_url "$DB_URL"
"$PG_DUMP_BIN" --format=custom --no-owner --no-acl --file="$TMP_OUT" 2> >(redact_pg_stderr)
mv "$TMP_OUT" "$OUT"
(cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$MANIFEST")")
chmod 600 "$OUT" "$MANIFEST"

echo "backup=$OUT"
echo "sha256=$MANIFEST"
if [[ -n "$RETENTION_DAYS" ]]; then
  prune_old_backups "$OUTPUT_DIR" "$RETENTION_DAYS"
fi
