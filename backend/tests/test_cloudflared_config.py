"""Cloudflare Tunnel ingress template regression tests."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "cloudflared-config.yml.example"


def test_cloudflared_template_routes_backend_paths_before_frontend():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    ingress = data["ingress"]
    frontend_index = next(i for i, rule in enumerate(ingress)
                          if rule.get("service") == "http://localhost:<FRONTEND_PORT>")

    backend_paths = {"^/api(/.*)?$", "^/health$", "^/readyz$"}
    edge_404_paths = {"^/docs$", "^/docs/.*$", "^/redoc$", r"^/openapi\.json$"}
    routed = {
        rule.get("path"): i
        for i, rule in enumerate(ingress)
        if rule.get("service") == "http://localhost:<BACKEND_PORT>"
    }
    denied = {
        rule.get("path"): i
        for i, rule in enumerate(ingress)
        if rule.get("service") == "http_status:404"
    }

    assert backend_paths <= set(routed)
    assert all(routed[path] < frontend_index for path in backend_paths)
    assert edge_404_paths <= set(denied)
    assert all(denied[path] < frontend_index for path in edge_404_paths)
    assert ingress[-1]["service"] == "http_status:404"


def test_cloudflared_template_path_regexes_do_not_overmatch_prefixes():
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    path_to_service = {
        rule["path"]: rule["service"]
        for rule in data["ingress"]
        if "path" in rule
    }

    intended_backend = {
        "/api": "http://localhost:<BACKEND_PORT>",
        "/api/sites": "http://localhost:<BACKEND_PORT>",
        "/health": "http://localhost:<BACKEND_PORT>",
        "/readyz": "http://localhost:<BACKEND_PORT>",
    }
    intended_edge_404 = {
        "/docs": "http_status:404",
        "/docs/oauth2-redirect": "http_status:404",
        "/redoc": "http_status:404",
        "/openapi.json": "http_status:404",
    }
    near_misses = [
        "/apiX",
        "/apix/sites",
        "/healthz",
        "/readyz-extra",
        "/docsABC",
        "/redocx",
        "/openapi.jsonx",
    ]

    def matching_services(path: str) -> list[str]:
        return [
            service
            for pattern, service in path_to_service.items()
            if re.search(pattern, path)
        ]

    for path, expected_service in {**intended_backend, **intended_edge_404}.items():
        assert matching_services(path)[0] == expected_service
    for path in near_misses:
        assert matching_services(path) == []
