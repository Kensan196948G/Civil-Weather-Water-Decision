"""systemd unit hardening regression tests."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNIT_DIR = ROOT / "deploy" / "systemd"
UNITS = (
    "cwwd-backend.service",
    "cwwd-frontend.service",
    "cwwd-tunnel.service",
    "cwwd-cloudflared-config-check.service",
    "cwwd-app-health-check.service",
    "cwwd-public-edge-access-check.service",
    "cwwd-security-surface-check.service",
    "cwwd-network-exposure-check.service",
    "cwwd-systemd-unit-drift-check.service",
    "cwwd-systemd-timer-freshness-check.service",
    "cwwd-secret-file-permission-check.service",
    "cwwd-ops-status.service",
    "cwwd-ops-status-json-export.service",
    "cwwd-ops-status-json-check.service",
    "cwwd-disk-space-check.service",
    "cwwd-db-backup.service",
    "cwwd-db-backup-export.service",
    "cwwd-db-backup-export-check.service",
    "cwwd-db-backup-check.service",
    "cwwd-db-backup-restore-drill.service",
    "cwwd-db-backup-failure@.service",
    "cwwd-ops-failure@.service",
)
MEMORY_DENY_WRITE_EXECUTE_UNITS = {
    "cwwd-frontend.service",
    "cwwd-tunnel.service",
}
NO_READ_WRITE_PATH_UNITS = {
    "cwwd-backend.service",
    "cwwd-frontend.service",
    "cwwd-tunnel.service",
    "cwwd-cloudflared-config-check.service",
    "cwwd-app-health-check.service",
    "cwwd-public-edge-access-check.service",
    "cwwd-security-surface-check.service",
    "cwwd-network-exposure-check.service",
    "cwwd-systemd-unit-drift-check.service",
    "cwwd-systemd-timer-freshness-check.service",
    "cwwd-secret-file-permission-check.service",
    "cwwd-ops-status.service",
    "cwwd-ops-status-json-export.service",
    "cwwd-ops-status-json-check.service",
    "cwwd-disk-space-check.service",
    "cwwd-db-backup-check.service",
    "cwwd-db-backup-export-check.service",
    "cwwd-db-backup-restore-drill.service",
    "cwwd-db-backup-failure@.service",
    "cwwd-ops-failure@.service",
}
AF_UNIX_ONLY_UNITS = {
    "cwwd-ops-status.service",
    "cwwd-disk-space-check.service",
    "cwwd-cloudflared-config-check.service",
    "cwwd-network-exposure-check.service",
    "cwwd-systemd-unit-drift-check.service",
    "cwwd-systemd-timer-freshness-check.service",
    "cwwd-secret-file-permission-check.service",
    "cwwd-db-backup-restore-drill.service",
    "cwwd-ops-status-json-export.service",
    "cwwd-ops-status-json-check.service",
}
BACKUP_DIR = "/var/backups/cwwd/postgres"
EXPORT_DIR = "/var/backups/cwwd/exports"
OPS_STATE_DIR = "/var/lib/cwwd"
PUBLIC_URL = "https://cwwd.mirai-dx-platform.com/"

REQUIRED_SERVICE_OPTIONS = {
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "PrivateDevices": "true",
    "ProtectSystem": "full",
    "ProtectHome": "read-only",
    "ProtectKernelTunables": "true",
    "ProtectKernelModules": "true",
    "ProtectKernelLogs": "true",
    "ProtectControlGroups": "true",
    "ProtectClock": "true",
    "ProtectHostname": "true",
    "RestrictSUIDSGID": "true",
    "RestrictRealtime": "true",
    "LockPersonality": "true",
    "SystemCallArchitectures": "native",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "KeyringMode": "private",
    "RemoveIPC": "true",
    "UMask": "0077",
}


def _service(path: Path):
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser["Service"]


def test_systemd_units_keep_hardening_baseline():
    for unit in UNITS:
        service = _service(UNIT_DIR / unit)
        for key, expected in REQUIRED_SERVICE_OPTIONS.items():
            assert service.get(key) == expected, f"{unit} missing {key}={expected}"
        if unit in AF_UNIX_ONLY_UNITS:
            assert service.get("RestrictAddressFamilies") == "AF_UNIX"
        else:
            assert service.get("RestrictAddressFamilies") == "AF_INET AF_INET6 AF_UNIX"
        if unit in MEMORY_DENY_WRITE_EXECUTE_UNITS:
            assert service.get("MemoryDenyWriteExecute") == "true"
        else:
            assert service.get("MemoryDenyWriteExecute") is None
        if unit in NO_READ_WRITE_PATH_UNITS:
            assert "ReadWritePaths" not in service


def test_db_backup_service_has_limited_write_scope_and_retention():
    service = _service(UNIT_DIR / "cwwd-db-backup.service")

    assert service.get("Type") == "oneshot"
    assert service.get("Restart") is None
    assert service.get("ReadWritePaths") == BACKUP_DIR
    unit_text = (UNIT_DIR / "cwwd-db-backup.service").read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/bin/test -f /home/kensan/.config/cwwd/db-backup.env" in unit_text
    assert f"ExecStartPre=/usr/bin/test -w {BACKUP_DIR}" in unit_text
    assert "ExecStartPre=/home/kensan/Projects/Mirai-DX-Project/Civil-Weather-Water-Decision/deploy/scripts/disk-space-check.sh" in unit_text
    assert "--data-min-free-mib 10240" in unit_text
    assert "--min-free-percent 15" in unit_text
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/db-backup.sh" in exec_start
    assert "--env-file /home/kensan/.config/cwwd/db-backup.env" in exec_start
    assert "--output-dir" in exec_start
    assert f"--output-dir {BACKUP_DIR}" in exec_start
    assert "--retention-days 14" in exec_start
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-db-backup.service", encoding="utf-8")
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-db-backup-failure@%n.service"


def test_app_health_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-app-health-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-app-health-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/app-health-check.sh" in exec_start
    assert "--backend-health-url http://127.0.0.1:55019/health" in exec_start
    assert "--backend-ready-url http://127.0.0.1:55019/readyz" in exec_start
    assert "--frontend-url http://127.0.0.1:34979/" in exec_start
    assert "--frontend-api-url http://127.0.0.1:34979/api/auth/me" in exec_start
    assert f"--public-url {PUBLIC_URL}" in exec_start
    assert "--public-statuses 302" in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_cloudflared_config_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-cloudflared-config-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-cloudflared-config-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/cloudflared-config-check.sh" in exec_start
    assert "--config /home/kensan/.cloudflared/config-cwwd.yml" in exec_start
    assert "--hostname cwwd.mirai-dx-platform.com" in exec_start
    assert "--backend-port 55019" in exec_start
    assert "--frontend-port 34979" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_cloudflared_config_check_timer_runs_periodic_snapshot():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-cloudflared-config-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "9min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "3min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-cloudflared-config-check.service"


def test_app_health_check_timer_runs_after_boot_then_every_five_minutes():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-app-health-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "2min"
    assert timer.get("OnUnitActiveSec") == "5min"
    assert timer.get("RandomizedDelaySec") == "30s"
    assert timer.get("AccuracySec") == "30s"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-app-health-check.service"


def test_public_edge_access_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-public-edge-access-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-public-edge-access-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/public-edge-access-check.sh" in exec_start
    assert "--base-url https://cwwd.mirai-dx-platform.com" in exec_start
    assert "--path /api/sites" in exec_start
    assert "--path /health" in exec_start
    assert "--path /readyz" in exec_start
    assert "--path /docs" in exec_start
    assert "--path /openapi.json" in exec_start
    assert "--expected-statuses 302" in exec_start
    assert "--location-contains cloudflareaccess.com/cdn-cgi/access/login" in exec_start
    assert "--timeout-seconds 10" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_public_edge_access_check_timer_runs_every_fifteen_minutes():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-public-edge-access-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "10min"
    assert timer.get("OnUnitActiveSec") == "15min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-public-edge-access-check.service"


def test_security_surface_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-security-surface-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-security-surface-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/security-surface-check.sh" in exec_start
    assert "--backend-url http://127.0.0.1:55019" in exec_start
    assert "--frontend-url http://127.0.0.1:34979/" in exec_start
    assert "--timeout-seconds 10" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_security_surface_check_timer_runs_every_fifteen_minutes():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-security-surface-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "4min"
    assert timer.get("OnUnitActiveSec") == "15min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-security-surface-check.service"


def test_network_exposure_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-network-exposure-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-network-exposure-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/network-exposure-check.sh" in exec_start
    assert "--port 55019" in exec_start
    assert "--port 34979" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_network_exposure_check_timer_runs_every_fifteen_minutes():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-network-exposure-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "6min"
    assert timer.get("OnUnitActiveSec") == "15min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-network-exposure-check.service"


def test_systemd_unit_drift_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-systemd-unit-drift-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-systemd-unit-drift-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/systemd-unit-drift-check.sh" in exec_start
    assert "--repo-dir /home/kensan/Projects/Mirai-DX-Project/Civil-Weather-Water-Decision/deploy/systemd" in exec_start
    assert "--system-dir /etc/systemd/system" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_systemd_unit_drift_check_timer_runs_periodic_snapshot():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-systemd-unit-drift-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "7min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "3min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-systemd-unit-drift-check.service"


def test_secret_file_permission_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-secret-file-permission-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-secret-file-permission-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/secret-file-permission-check.sh" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_secret_file_permission_check_timer_runs_periodic_snapshot():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-secret-file-permission-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "8min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "3min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-secret-file-permission-check.service"


def test_systemd_timer_freshness_check_service_has_no_secret_env_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-systemd-timer-freshness-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-systemd-timer-freshness-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/systemd-timer-freshness-check.sh" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_systemd_timer_freshness_check_timer_runs_periodic_snapshot():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-systemd-timer-freshness-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "12min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "3min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-systemd-timer-freshness-check.service"


def test_ops_status_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-ops-status.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-ops-status.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/ops-status.sh" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_ops_status_timer_runs_periodic_snapshot_without_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-ops-status.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "5min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-ops-status.service"


def test_ops_status_json_export_service_has_limited_state_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-ops-status-json-export.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-ops-status-json-export.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert service.get("EnvironmentFile") is None
    assert service.get("StateDirectory") == "cwwd"
    assert service.get("StateDirectoryMode") == "0750"
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/ops-status-json-export.sh" in exec_start
    assert f"--output {OPS_STATE_DIR}/ops-status.json" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_ops_status_json_export_timer_runs_periodic_snapshot_without_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-ops-status-json-export.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "6min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-ops-status-json-export.service"


def test_ops_status_json_check_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-ops-status-json-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-ops-status-json-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert service.get("EnvironmentFile") is None
    assert "ReadWritePaths" not in service
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/ops-status-json-check.sh" in exec_start
    assert f"--path {OPS_STATE_DIR}/ops-status.json" in exec_start
    assert "--max-age-minutes 60" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert "SLACK_WEBHOOK_URL" not in exec_start
    assert "TEAMS_WEBHOOK_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_ops_status_json_check_timer_runs_periodic_snapshot_without_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-ops-status-json-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "8min"
    assert timer.get("OnUnitActiveSec") == "30min"
    assert timer.get("RandomizedDelaySec") == "2min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-ops-status-json-check.service"


def test_disk_space_check_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-disk-space-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-disk-space-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert service.get("EnvironmentFile") is None
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/disk-space-check.sh" in exec_start
    assert "--path /" in exec_start
    assert f"--path {BACKUP_DIR}" in exec_start
    assert f"--path {EXPORT_DIR}" in exec_start
    assert "--root-min-free-mib 4096" in exec_start
    assert "--data-min-free-mib 10240" in exec_start
    assert "--min-free-percent 15" in exec_start
    assert "--min-inode-free-percent 10" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-ops-failure@%n.service"


def test_disk_space_check_timer_runs_hourly_without_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-disk-space-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnBootSec") == "3min"
    assert timer.get("OnUnitActiveSec") == "1h"
    assert timer.get("RandomizedDelaySec") == "5min"
    assert timer.get("AccuracySec") == "1min"
    assert timer.get("Persistent") == "false"
    assert timer.get("Unit") == "cwwd-disk-space-check.service"


def test_db_backup_timer_runs_daily_with_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-db-backup.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnCalendar") == "*-*-* 02:10:00"
    assert timer.get("RandomizedDelaySec") == "30min"
    assert timer.get("AccuracySec") == "5min"
    assert timer.get("Persistent") == "true"
    assert timer.get("Unit") == "cwwd-db-backup.service"


def test_db_backup_export_service_has_limited_write_scope_and_retention():
    service = _service(UNIT_DIR / "cwwd-db-backup-export.service")
    unit_text = (UNIT_DIR / "cwwd-db-backup-export.service").read_text(encoding="utf-8")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-db-backup-export.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert service.get("ReadWritePaths") == EXPORT_DIR
    assert "ExecStartPre=/usr/bin/test -f /home/kensan/.config/cwwd/backup-export.passphrase" in unit_text
    assert f"ExecStartPre=/usr/bin/test -r {BACKUP_DIR}" in unit_text
    assert f"ExecStartPre=/usr/bin/test -w {EXPORT_DIR}" in unit_text
    assert "ExecStartPre=/home/kensan/Projects/Mirai-DX-Project/Civil-Weather-Water-Decision/deploy/scripts/disk-space-check.sh" in unit_text
    assert "--root-min-free-mib 4096" in unit_text
    assert "--data-min-free-mib 10240" in unit_text
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/db-backup-export.sh" in exec_start
    assert f"--backup-dir {BACKUP_DIR}" in exec_start
    assert f"--output-dir {EXPORT_DIR}" in exec_start
    assert "--passphrase-file /home/kensan/.config/cwwd/backup-export.passphrase" in exec_start
    assert "--retention-days 30" in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-db-backup-failure@%n.service"


def test_db_backup_export_timer_runs_daily_after_backup_window():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-db-backup-export.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnCalendar") == "*-*-* 03:10:00"
    assert timer.get("RandomizedDelaySec") == "30min"
    assert timer.get("AccuracySec") == "5min"
    assert timer.get("Persistent") == "true"
    assert timer.get("Unit") == "cwwd-db-backup-export.service"


def test_db_backup_export_check_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-db-backup-export-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-db-backup-export-check.service", encoding="utf-8")
    unit_text = (UNIT_DIR / "cwwd-db-backup-export-check.service").read_text(encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert "ExecStartPre=/usr/bin/test -f /home/kensan/.config/cwwd/backup-export.passphrase" in unit_text
    assert f"ExecStartPre=/usr/bin/test -r {EXPORT_DIR}" in unit_text
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/db-backup-export-check.sh" in exec_start
    assert f"--export-dir {EXPORT_DIR}" in exec_start
    assert "--warn-age-hours 26" in exec_start
    assert "--max-age-hours 28" in exec_start
    assert "--passphrase-file /home/kensan/.config/cwwd/backup-export.passphrase" in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-db-backup-failure@%n.service"


def test_db_backup_export_check_timer_runs_hourly_after_export_check_window():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-db-backup-export-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnCalendar") == "*-*-* *:47:00"
    assert timer.get("RandomizedDelaySec") == "10min"
    assert timer.get("AccuracySec") == "2min"
    assert timer.get("Persistent") == "true"
    assert timer.get("Unit") == "cwwd-db-backup-export-check.service"


def test_db_backup_check_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-db-backup-check.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-db-backup-check.service", encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/db-backup-check.sh" in exec_start
    assert f"--backup-dir {BACKUP_DIR}" in exec_start
    assert "--warn-age-hours 24" in exec_start
    assert "--max-age-hours 26" in exec_start
    assert "--env-file /home/kensan/.config/cwwd/db-backup.env" in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-db-backup-failure@%n.service"


def test_db_backup_check_timer_runs_hourly_with_persistent_catchup():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-db-backup-check.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnCalendar") == "*-*-* *:17:00"
    assert timer.get("RandomizedDelaySec") == "10min"
    assert timer.get("AccuracySec") == "2min"
    assert timer.get("Persistent") == "true"
    assert timer.get("Unit") == "cwwd-db-backup-check.service"


def test_db_backup_restore_drill_service_has_no_write_scope_and_failure_alert():
    service = _service(UNIT_DIR / "cwwd-db-backup-restore-drill.service")
    unit_parser = ConfigParser(strict=False, delimiters=("=",))
    unit_parser.optionxform = str
    unit_parser.read(UNIT_DIR / "cwwd-db-backup-restore-drill.service", encoding="utf-8")
    unit_text = (UNIT_DIR / "cwwd-db-backup-restore-drill.service").read_text(encoding="utf-8")

    assert service.get("Type") == "oneshot"
    assert "ReadWritePaths" not in service
    assert f"ExecStartPre=/usr/bin/test -r {BACKUP_DIR}" in unit_text
    assert "UnsetEnvironment=DATABASE_URL DATABASE_URL_DIRECT PGPASSWORD" in unit_text
    exec_start = service.get("ExecStart", "")
    assert "deploy/scripts/db-backup-restore-drill.sh" in exec_start
    assert f"--backup-dir {BACKUP_DIR}" in exec_start
    assert "--warn-age-hours 26" in exec_start
    assert "--max-age-hours 30" in exec_start
    assert "DATABASE_URL" not in exec_start
    assert unit_parser["Unit"].get("OnFailure", raw=True) == "cwwd-db-backup-failure@%n.service"


def test_db_backup_restore_drill_timer_runs_daily_after_backup_window():
    parser = ConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str
    parser.read(UNIT_DIR / "cwwd-db-backup-restore-drill.timer", encoding="utf-8")

    timer = parser["Timer"]
    assert timer.get("OnCalendar") == "*-*-* 04:20:00"
    assert timer.get("RandomizedDelaySec") == "30min"
    assert timer.get("AccuracySec") == "5min"
    assert timer.get("Persistent") == "true"
    assert timer.get("Unit") == "cwwd-db-backup-restore-drill.service"


def test_failure_services_use_ops_alert_with_optional_env():
    for unit in ("cwwd-db-backup-failure@.service", "cwwd-ops-failure@.service"):
        service = _service(UNIT_DIR / unit)
        unit_text = (UNIT_DIR / unit).read_text(encoding="utf-8")

        assert service.get("Type") == "oneshot"
        assert "EnvironmentFile=-/home/kensan/.config/cwwd/ops-alert.env" in unit_text
        assert "After=network-online.target" in unit_text
        assert "Wants=network-online.target" in unit_text
        exec_start = service.get("ExecStart", raw=True)
        assert "deploy/scripts/ops-alert.sh" in exec_start
        assert "%i" in exec_start
        assert "--severity alert" in exec_start
        assert "--max-chars 3500" in exec_start
        assert "SLACK_WEBHOOK_URL" not in exec_start
        assert "TEAMS_WEBHOOK_URL" not in exec_start
