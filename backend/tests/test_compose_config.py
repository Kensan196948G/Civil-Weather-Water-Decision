"""Docker Compose contract tests."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"


def _compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_frontend_uses_backend_proxy_without_lan_api_origin():
    frontend = _compose()["services"]["frontend"]
    env = frontend["environment"]

    assert env["CW_BACKEND_PROXY_BASE"] == "http://backend:8000"
    assert env["CW_BACKEND_PROXY_ALLOWED_HOSTS"] == "backend"
    assert env["CW_TILE_URL"] == "none"
    assert frontend["ports"] == ["127.0.0.1:${FRONTEND_HOST_PORT:-3000}:3000"]


def test_compose_frontend_waits_for_backend_health():
    services = _compose()["services"]
    backend = services["backend"]
    frontend = services["frontend"]

    backend_health = backend["healthcheck"]
    assert "http://127.0.0.1:8000/health" in backend_health["test"][-1]
    assert backend_health["interval"] == "5s"
    assert backend_health["timeout"] == "5s"
    assert backend_health["retries"] == 20
    assert backend_health["start_period"] == "20s"
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"
