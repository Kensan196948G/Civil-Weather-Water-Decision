#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: cloudflared-config-check.sh [options]

Checks the production Cloudflare Tunnel config structure without reading credentials.
  --config PATH          default /home/kensan/.cloudflared/config-cwwd.yml
  --hostname HOST        default cwwd.mirai-dx-platform.com
  --tunnel-id ID         default 07c9bda3-b4ad-46ae-8401-4b677de3c8a4
  --backend-port PORT    default 55019
  --frontend-port PORT   default 34979
USAGE
}

CONFIG="${CWWD_CLOUDFLARED_CONFIG:-/home/kensan/.cloudflared/config-cwwd.yml}"
HOSTNAME="${CWWD_PUBLIC_HOSTNAME:-cwwd.mirai-dx-platform.com}"
TUNNEL_ID="${CWWD_TUNNEL_ID:-07c9bda3-b4ad-46ae-8401-4b677de3c8a4}"
BACKEND_PORT="${CWWD_BACKEND_PORT:-55019}"
FRONTEND_PORT="${CWWD_FRONTEND_PORT:-34979}"

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_port() {
  [[ "$1" =~ ^[0-9]+$ && "$1" -ge 1 && "$1" -le 65535 ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="${2:?--config requires a path}"
      shift 2
      ;;
    --hostname)
      HOSTNAME="${2:?--hostname requires a host name}"
      shift 2
      ;;
    --tunnel-id)
      TUNNEL_ID="${2:?--tunnel-id requires an id}"
      shift 2
      ;;
    --backend-port)
      BACKEND_PORT="${2:?--backend-port requires a port}"
      shift 2
      ;;
    --frontend-port)
      FRONTEND_PORT="${2:?--frontend-port requires a port}"
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

[[ -f "$CONFIG" ]] || fail "cloudflared config not found" 2
[[ -r "$CONFIG" ]] || fail "cloudflared config is not readable" 2
is_port "$BACKEND_PORT" || fail "--backend-port must be an integer between 1 and 65535" 2
is_port "$FRONTEND_PORT" || fail "--frontend-port must be an integer between 1 and 65535" 2
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

python3 - "$CONFIG" "$HOSTNAME" "$TUNNEL_ID" "$BACKEND_PORT" "$FRONTEND_PORT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
expected_hostname = sys.argv[2]
expected_tunnel_id = sys.argv[3]
backend_service = f"http://localhost:{sys.argv[4]}"
frontend_service = f"http://localhost:{sys.argv[5]}"
backend_paths = {"^/api(/.*)?$", "^/health$", "^/readyz$"}
edge_404_paths = {"^/docs$", "^/docs/.*$", "^/redoc$", r"^/openapi\.json$"}
near_misses = [
    "/apiX",
    "/apix/sites",
    "/healthz",
    "/readyz-extra",
    "/docsABC",
    "/redocx",
    "/openapi.jsonx",
]


def parse_scalar(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    return key.strip(), value.strip().strip("\"'")


def parse_config(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    top: dict[str, str] = {}
    ingress: list[dict[str, str]] = []
    in_ingress = False
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "ingress:":
            in_ingress = True
            continue
        if not in_ingress:
            item = parse_scalar(stripped)
            if item:
                top[item[0]] = item[1]
            continue
        if stripped.startswith("- "):
            if current is not None:
                ingress.append(current)
            current = {}
            item = parse_scalar(stripped[2:].strip())
            if item:
                current[item[0]] = item[1]
            continue
        if current is not None:
            item = parse_scalar(stripped)
            if item:
                current[item[0]] = item[1]
    if current is not None:
        ingress.append(current)
    return top, ingress


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(3)


top, ingress = parse_config(config_path)
if top.get("tunnel") != expected_tunnel_id:
    fail("tunnel_id_mismatch")
credentials = top.get("credentials-file") or ""
if not credentials:
    fail("credentials_file_missing")
credential_path = Path(credentials).expanduser()
if not credential_path.is_file():
    fail("credentials_file_not_found")

if len(ingress) < 2:
    fail("ingress_rules_missing")
if ingress[-1].get("service") != "http_status:404":
    fail("catch_all_404_missing")

frontend_indexes = [
    index for index, rule in enumerate(ingress)
    if rule.get("hostname") == expected_hostname and rule.get("service") == frontend_service and "path" not in rule
]
if len(frontend_indexes) != 1:
    fail("frontend_rule_mismatch")
frontend_index = frontend_indexes[0]

routed = {
    rule.get("path"): index
    for index, rule in enumerate(ingress)
    if rule.get("hostname") == expected_hostname and rule.get("service") == backend_service
}
denied = {
    rule.get("path"): index
    for index, rule in enumerate(ingress)
    if rule.get("hostname") == expected_hostname and rule.get("service") == "http_status:404"
}
if not backend_paths <= set(routed):
    fail("backend_paths_missing")
if not edge_404_paths <= set(denied):
    fail("edge_404_paths_missing")
if any(routed[path] > frontend_index for path in backend_paths):
    fail("backend_paths_after_frontend")
if any(denied[path] > frontend_index for path in edge_404_paths):
    fail("edge_404_paths_after_frontend")

path_to_service = {
    rule["path"]: rule["service"]
    for rule in ingress
    if rule.get("hostname") == expected_hostname and "path" in rule
}

def matching_services(path: str) -> list[str]:
    return [
        service
        for pattern, service in path_to_service.items()
        if re.search(pattern, path)
    ]

for path, expected in {
    "/api": backend_service,
    "/api/sites": backend_service,
    "/health": backend_service,
    "/readyz": backend_service,
    "/docs": "http_status:404",
    "/docs/oauth2-redirect": "http_status:404",
    "/redoc": "http_status:404",
    "/openapi.json": "http_status:404",
}.items():
    services = matching_services(path)
    if not services or services[0] != expected:
        fail(f"path_route_mismatch={path}")

for path in near_misses:
    if matching_services(path):
        fail(f"path_regex_overmatch={path}")

print(f"ingress_rules={len(ingress)}")
print(f"backend_paths={len(backend_paths)}")
print(f"edge_404_paths={len(edge_404_paths)}")
print("credentials_file=present")
print("status=ok")
PY
