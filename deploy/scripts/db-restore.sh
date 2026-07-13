#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: db-restore.sh --dump FILE [--env-file PATH] [--dry-run] [--allow-missing-checksum]

Restores a PostgreSQL custom-format dump with pg_restore.
Destructive restore requires CWWD_RESTORE_CONFIRM=RESTORE.
DATABASE_URL may be provided by the environment or by --env-file.
By default, FILE.sha256 must exist and pass sha256sum -c before restore.
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
      DATABASE_URL|DATABASE_URL_DIRECT|PG_RESTORE_BIN)
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

default_pg_restore_bin() {
  local candidates=()
  local path
  for path in /usr/lib/postgresql/*/bin/pg_restore; do
    [[ -x "$path" ]] && candidates+=("$path")
  done
  if [[ ${#candidates[@]} -gt 0 ]]; then
    printf '%s\n' "${candidates[@]}" | sort -V | tail -n 1
  else
    printf '%s\n' pg_restore
  fi
}

verify_checksum() {
  local checksum_file="$1"
  local manifest_target
  manifest_target="$(awk 'NR == 1 { print $2 }' "$checksum_file")"
  if [[ "$manifest_target" == */* ]]; then
    sha256sum -c "$checksum_file"
  else
    (cd "$(dirname "$checksum_file")" && sha256sum -c "$(basename "$checksum_file")")
  fi
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
DUMP_FILE=""
DRY_RUN=false
ALLOW_MISSING_CHECKSUM=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dump)
      DUMP_FILE="${2:?--dump requires a path}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --allow-missing-checksum)
      ALLOW_MISSING_CHECKSUM=true
      shift
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

if [[ -z "$DUMP_FILE" || ! -f "$DUMP_FILE" ]]; then
  echo "--dump FILE is required and must exist" >&2
  exit 2
fi

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
  echo "DATABASE_URL must be PostgreSQL for restore" >&2
  exit 2
fi

if [[ "$DB_URL" == *"-pooler."* ]]; then
  echo "Use DATABASE_URL_DIRECT with a direct/unpooled Neon URL for pg_restore, not a pooled -pooler URL" >&2
  exit 2
fi

unset DATABASE_URL DATABASE_URL_DIRECT

PG_RESTORE_BIN="${PG_RESTORE_BIN:-$(default_pg_restore_bin)}"

command -v "$PG_RESTORE_BIN" >/dev/null 2>&1 || {
  echo "pg_restore is required but was not found in PATH" >&2
  exit 127
}

CHECKSUM_FILE="${DUMP_FILE}.sha256"

if [[ -f "$CHECKSUM_FILE" ]]; then
  verify_checksum "$CHECKSUM_FILE"
elif [[ "$ALLOW_MISSING_CHECKSUM" == true ]]; then
  echo "WARNING: ${CHECKSUM_FILE} not found; continuing because --allow-missing-checksum was set" >&2
else
  echo "Missing checksum manifest: ${CHECKSUM_FILE}. Refusing restore without integrity verification." >&2
  echo "Use --allow-missing-checksum only for explicitly approved emergency recovery." >&2
  exit 2
fi

if [[ "$DRY_RUN" == true ]]; then
  "$PG_RESTORE_BIN" --list "$DUMP_FILE" >/dev/null 2> >(redact_pg_stderr)
  echo "dry_run=ok"
  exit 0
fi

if [[ "${CWWD_RESTORE_CONFIRM:-}" != "RESTORE" ]]; then
  echo "Refusing destructive restore. Set CWWD_RESTORE_CONFIRM=RESTORE to continue." >&2
  exit 3
fi

cleanup() {
  if [[ -n "$TMP_PGPASSFILE" ]]; then
    rm -f "$TMP_PGPASSFILE"
  fi
}
trap cleanup EXIT

configure_libpq_env_from_url "$DB_URL"
"$PG_RESTORE_BIN" --single-transaction --exit-on-error --clean --if-exists --no-owner --no-acl --dbname="$PGDATABASE" "$DUMP_FILE" 2> >(redact_pg_stderr)
echo "restore=ok"
