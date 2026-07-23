#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: network-exposure-check.sh [options]

Checks that production app ports are listening only on loopback addresses:
  --port N          port to check; can be repeated (default: 55019 and 34979)
  --tcp-file PATH   default /proc/net/tcp
  --tcp6-file PATH  default /proc/net/tcp6
USAGE
}

PORTS=()
TCP_FILE="/proc/net/tcp"
TCP6_FILE="/proc/net/tcp6"

fail() {
  echo "$1" >&2
  exit "${2:-2}"
}

is_port() {
  [[ "$1" =~ ^[0-9]+$ && "$1" -ge 1 && "$1" -le 65535 ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      is_port "${2:-}" || fail "--port must be an integer between 1 and 65535" 2
      PORTS+=("$2")
      shift 2
      ;;
    --tcp-file)
      TCP_FILE="${2:?--tcp-file requires a path}"
      shift 2
      ;;
    --tcp6-file)
      TCP6_FILE="${2:?--tcp6-file requires a path}"
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

if [[ "${#PORTS[@]}" -eq 0 ]]; then
  PORTS=(55019 34979)
fi
[[ -r "$TCP_FILE" ]] || fail "TCP file is not readable: $TCP_FILE" 2
[[ -r "$TCP6_FILE" ]] || fail "TCP6 file is not readable: $TCP6_FILE" 2
command -v python3 >/dev/null 2>&1 || fail "python3 is required but was not found in PATH" 127

ports_csv="$(IFS=,; printf '%s' "${PORTS[*]}")"

python3 - "$ports_csv" "$TCP_FILE" "$TCP6_FILE" <<'PY'
from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path

ports = [int(value) for value in sys.argv[1].split(",") if value]
tcp_file = Path(sys.argv[2])
tcp6_file = Path(sys.argv[3])
LISTEN = "0A"


def ipv4_from_proc(value: str) -> str:
    raw = bytes.fromhex(value)
    return socket.inet_ntop(socket.AF_INET, raw[::-1])


def ipv6_from_proc(value: str) -> str:
    raw = bytes.fromhex(value)
    reordered = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
    return socket.inet_ntop(socket.AF_INET6, reordered)


def parse_proc(path: Path, family: int) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    with path.open(encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            fields = line.split()
            if len(fields) < 4 or fields[3] != LISTEN:
                continue
            local = fields[1]
            try:
                address_hex, port_hex = local.split(":", 1)
                port = int(port_hex, 16)
                if family == socket.AF_INET:
                    address = ipv4_from_proc(address_hex)
                else:
                    address = ipv6_from_proc(address_hex)
            except ValueError:
                raise SystemExit(f"Malformed socket row in {path.name}") from None
            rows.append((port, address))
    return rows


listeners_by_port: dict[int, list[str]] = {port: [] for port in ports}
for port, address in parse_proc(tcp_file, socket.AF_INET) + parse_proc(tcp6_file, socket.AF_INET6):
    if port in listeners_by_port:
        listeners_by_port[port].append(address)

failed = False
for port in ports:
    port_failed = False
    listeners = sorted(set(listeners_by_port[port]))
    if not listeners:
        print(f"port_{port}_listeners=missing", file=sys.stderr)
        port_failed = True
        failed = True
        continue
    print(f"port_{port}_listeners={','.join(listeners)}")
    for address in listeners:
        if not ipaddress.ip_address(address).is_loopback:
            print(f"port_{port}_non_loopback_listener={address}", file=sys.stderr)
            port_failed = True
            failed = True
    if not port_failed:
        print(f"port_{port}_exposure=ok")

if failed:
    raise SystemExit(3)
print("status=ok")
PY
