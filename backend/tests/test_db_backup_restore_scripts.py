"""DB backup/restore shell scripts の非ライブ検証。"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_HEALTH = ROOT / "deploy" / "scripts" / "app-health-check.sh"
CLOUDFLARED_CONFIG_CHECK = ROOT / "deploy" / "scripts" / "cloudflared-config-check.sh"
SECURITY_SURFACE = ROOT / "deploy" / "scripts" / "security-surface-check.sh"
BACKUP = ROOT / "deploy" / "scripts" / "db-backup.sh"
EXPORT = ROOT / "deploy" / "scripts" / "db-backup-export.sh"
EXPORT_CHECK = ROOT / "deploy" / "scripts" / "db-backup-export-check.sh"
RESTORE = ROOT / "deploy" / "scripts" / "db-restore.sh"
CHECK = ROOT / "deploy" / "scripts" / "db-backup-check.sh"
RESTORE_DRILL = ROOT / "deploy" / "scripts" / "db-backup-restore-drill.sh"
DISK_CHECK = ROOT / "deploy" / "scripts" / "disk-space-check.sh"
NETWORK_EXPOSURE = ROOT / "deploy" / "scripts" / "network-exposure-check.sh"
OPS_ALERT = ROOT / "deploy" / "scripts" / "ops-alert.sh"
OPS_FAILED_REPORT = ROOT / "deploy" / "scripts" / "ops-failed-units-report.sh"
OPS_STATUS = ROOT / "deploy" / "scripts" / "ops-status.sh"
OPS_STATUS_JSON_CHECK = ROOT / "deploy" / "scripts" / "ops-status-json-check.sh"
OPS_STATUS_JSON_EXPORT = ROOT / "deploy" / "scripts" / "ops-status-json-export.sh"
PUBLIC_EDGE = ROOT / "deploy" / "scripts" / "public-edge-access-check.sh"
SECRET_PERMS = ROOT / "deploy" / "scripts" / "secret-file-permission-check.sh"
TIMER_FRESHNESS = ROOT / "deploy" / "scripts" / "systemd-timer-freshness-check.sh"
SYSTEMD_DRIFT = ROOT / "deploy" / "scripts" / "systemd-unit-drift-check.sh"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
    }
    env.update(extra)
    if (tmp_path / "pg_dump").exists():
        env.setdefault("PG_DUMP_BIN", str(tmp_path / "pg_dump"))
    if (tmp_path / "pg_restore").exists():
        env.setdefault("PG_RESTORE_BIN", str(tmp_path / "pg_restore"))
    return env


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _write_dump_with_checksum(path: Path, body: str = "dump") -> None:
    path.write_text(body)
    path.chmod(0o600)
    digest = subprocess.run(["sha256sum", path.name],
                            cwd=path.parent, text=True, capture_output=True, check=True).stdout
    Path(f"{path}.sha256").write_text(digest)
    Path(f"{path}.sha256").chmod(0o600)


def _write_export_with_checksum(path: Path, body: str = "encrypted") -> None:
    path.write_text(body)
    path.chmod(0o600)
    digest = subprocess.run(["sha256sum", path.name],
                            cwd=path.parent, text=True, capture_output=True, check=True).stdout
    Path(f"{path}.sha256").write_text(digest)
    Path(f"{path}.sha256").chmod(0o600)


def test_deploy_shell_scripts_are_executable():
    scripts = sorted((ROOT / "deploy" / "scripts").glob("*.sh"))
    assert scripts
    for script in scripts:
        assert script.stat().st_mode & 0o111, f"{script} is not executable"


def test_backup_rejects_missing_database_url(tmp_path):
    env = _env(tmp_path)
    env.pop("DATABASE_URL", None)

    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out")],
                       env=env, text=True, capture_output=True)

    assert r.returncode == 2
    assert "DATABASE_URL_DIRECT or DATABASE_URL is required" in r.stderr


def test_backup_rejects_sqlite_database_url(tmp_path):
    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out")],
                       env=_env(tmp_path, DATABASE_URL="sqlite:///x.db"),
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "PostgreSQL" in r.stderr


def test_backup_rejects_neon_pooler_url(tmp_path):
    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out")],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@ep-test-pooler.neon.tech/db"),
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "direct/unpooled" in r.stderr


def test_backup_uses_pg_dump_and_writes_checksum(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")

    out_dir = tmp_path / "backups"
    r = subprocess.run([str(BACKUP), "--output-dir", str(out_dir)],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    dump = next(out_dir.glob("*.dump"))
    assert dump.exists()
    assert dump.with_suffix(".dump.sha256").exists()
    assert f"  {dump.name}" in dump.with_suffix(".dump.sha256").read_text()
    assert "--format=custom" in calls.read_text()
    assert "postgresql://" not in calls.read_text()


def test_backup_env_file_accepts_url_query_without_shell_sourcing(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\nPGSSLMODE=%s\\nPGCHANNELBINDING=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" "$PGSSLMODE" "$PGCHANNELBINDING" >> "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://u:p@db.example.com/cwwd?sslmode=require&channel_binding=require\n")

    r = subprocess.run([str(BACKUP), "--env-file", str(env_file), "--output-dir", str(tmp_path / "out")],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    call_text = calls.read_text()
    assert "PGCHANNELBINDING=require" in call_text
    assert "PGSSLMODE=require" in call_text
    assert "postgresql://" not in call_text


def test_backup_env_file_scrubs_inherited_database_url_direct(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://env-file:pw@db.example.com/cwwd\n")

    r = subprocess.run([str(BACKUP), "--env-file", str(env_file), "--output-dir", str(tmp_path / "out")],
                       env=_env(
                           tmp_path,
                           DATABASE_URL_DIRECT="postgresql://stale:pw@ep-test-pooler.neon.tech/cwwd",
                       ),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    call_text = calls.read_text()
    assert "PGUSER=env-file" in call_text
    assert "PGHOST=db.example.com" in call_text
    assert "stale:pw@ep-test-pooler" not in call_text
    assert "postgresql://" not in call_text


def test_backup_prefers_database_url_direct_over_pooled_database_url(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")

    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out")],
                       env=_env(
                           tmp_path,
                           DATABASE_URL="postgresql://u:p@ep-test-pooler.neon.tech/cwwd",
                           DATABASE_URL_DIRECT="postgresql://u:p@ep-test.neon.tech/cwwd",
                       ),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    call_text = calls.read_text()
    assert "PGHOST=ep-test.neon.tech" in call_text
    assert "pooler" not in call_text
    assert "postgresql://" not in call_text


def test_backup_normalizes_sqlalchemy_postgresql_driver_url(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")

    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out")],
                       env=_env(tmp_path, DATABASE_URL="postgresql+psycopg2://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    call_text = calls.read_text()
    assert "PGHOST=db.example.com" in call_text
    assert "PGDATABASE=cwwd" in call_text
    assert "postgresql://" not in call_text
    assert "postgresql+psycopg2" not in call_text


def test_backup_rejects_invalid_retention_days(tmp_path):
    r = subprocess.run([str(BACKUP), "--output-dir", str(tmp_path / "out"), "--retention-days", "0"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "positive integer" in r.stderr


def test_backup_retention_prunes_only_old_cwwd_dump_artifacts_after_success(tmp_path):
    calls = tmp_path / "pg_dump.calls"
    _write_exe(tmp_path / "pg_dump", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--file="* ]]; then
    out="${{1#--file=}}"
    printf 'dump-data' > "$out"
  fi
  shift
done
""")
    out_dir = tmp_path / "backups"
    out_dir.mkdir()
    old_dump = out_dir / "cwwd-20000101T000000Z.dump"
    old_manifest = out_dir / "cwwd-20000101T000000Z.dump.sha256"
    unrelated = out_dir / "manual-note.txt"
    old_dump.write_text("old")
    old_manifest.write_text("old-hash  cwwd-20000101T000000Z.dump\n")
    unrelated.write_text("keep")
    old_time = time.time() - (3 * 24 * 60 * 60)
    os.utime(old_dump, (old_time, old_time))
    os.utime(old_manifest, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    r = subprocess.run(
        [str(BACKUP), "--output-dir", str(out_dir), "--retention-days", "1"],
        env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    assert "retention_deleted=2" in r.stdout
    assert not old_dump.exists()
    assert not old_manifest.exists()
    assert unrelated.exists()
    assert len(list(out_dir.glob("cwwd-*.dump"))) == 1


def test_backup_retention_does_not_prune_when_dump_fails(tmp_path):
    _write_exe(tmp_path / "pg_dump", "#!/usr/bin/env bash\nexit 9\n")
    out_dir = tmp_path / "backups"
    out_dir.mkdir()
    old_dump = out_dir / "cwwd-20000101T000000Z.dump"
    old_dump.write_text("old")
    old_time = time.time() - (3 * 24 * 60 * 60)
    os.utime(old_dump, (old_time, old_time))

    r = subprocess.run(
        [str(BACKUP), "--output-dir", str(out_dir), "--retention-days", "1"],
        env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 9
    assert "retention_deleted" not in r.stdout
    assert old_dump.exists()


def test_backup_check_accepts_fresh_dump_with_valid_checksum(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"latest_dump={dump}" in r.stdout
    assert "checksum=ok" in r.stdout


def test_backup_check_rejects_missing_dump(tmp_path):
    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "No cwwd-YYYYMMDDTHHMMSSZ.dump files found" in r.stderr


def test_backup_check_rejects_missing_checksum(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    dump.write_text("dump")
    dump.chmod(0o600)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "missing checksum manifest" in r.stderr


def test_backup_check_rejects_checksum_mismatch(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    dump.write_text("tampered")

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 1
    assert "FAILED" in r.stdout
    assert "computed checksum did NOT match" in r.stderr


def test_backup_check_rejects_stale_latest_dump(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    old_time = time.time() - (3 * 60 * 60)
    os.utime(dump, (old_time, old_time))
    os.utime(Path(f"{dump}.sha256"), (old_time, old_time))

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path),
                        "--warn-age-hours", "1", "--max-age-hours", "2"],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "Latest backup is stale" in r.stderr
    assert "max_age_seconds=7200" in r.stderr


def test_backup_check_rejects_orphan_manifest(tmp_path):
    manifest = tmp_path / "cwwd-20260713T000000Z.dump.sha256"
    manifest.write_text("hash  cwwd-20260713T000000Z.dump\n")
    manifest.chmod(0o600)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest is missing dump" in r.stderr


def test_backup_check_rejects_manifest_target_mismatch(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    Path(f"{dump}.sha256").write_text("hash  other.dump\n")
    Path(f"{dump}.sha256").chmod(0o600)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest target mismatch" in r.stderr


def test_backup_check_rejects_zero_byte_dump(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    dump.write_text("")
    dump.chmod(0o600)
    Path(f"{dump}.sha256").write_text("hash  cwwd-20260713T000000Z.dump\n")
    Path(f"{dump}.sha256").chmod(0o600)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Zero-byte dump" in r.stderr


def test_backup_check_rejects_stale_tmp_file(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    tmp = tmp_path / "cwwd-20260713T010000Z.dump.tmp"
    tmp.write_text("partial")
    tmp.chmod(0o600)
    old_time = time.time() - (2 * 60 * 60)
    os.utime(tmp, (old_time, old_time))

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Stale temporary backup file present" in r.stderr


def test_backup_check_rejects_malformed_dump_name(tmp_path):
    dump = tmp_path / "cwwd-latest.dump"
    dump.write_text("dump")
    dump.chmod(0o600)
    Path(f"{dump}.sha256").write_text("hash  cwwd-latest.dump\n")
    Path(f"{dump}.sha256").chmod(0o600)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Malformed dump filename" in r.stderr


def test_backup_check_rejects_unsafe_file_permissions(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    dump.chmod(0o644)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr


def test_backup_check_validates_env_file_permissions_without_reading_it(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    env_file = tmp_path / "db-backup.env"
    env_file.write_text("DATABASE_URL_DIRECT=postgresql://secret:secret@db.example.com/cwwd\n")
    env_file.chmod(0o644)

    r = subprocess.run([str(CHECK), "--backup-dir", str(tmp_path), "--max-age-hours", "26",
                        "--env-file", str(env_file)],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr
    assert "secret" not in r.stderr


def _write_fake_df(tmp_path: Path, *, size: int = 10_000, free: int = 5_000,
                   inodes: int = 1_000, inode_free: int = 500) -> None:
    _write_exe(tmp_path / "df", f"""#!/usr/bin/env bash
if [[ "$1" == "-Pi" ]]; then
  printf 'Filesystem Inodes IUsed IFree IUse%% Mounted on\\n'
  printf 'fakefs {inodes} 0 {inode_free} 50%% %s\\n' "${{@: -1}}"
else
  printf 'Filesystem 1B-blocks Used Available Use%% Mounted on\\n'
  printf 'fakefs {size} 0 {free} 50%% %s\\n' "${{@: -1}}"
fi
""")


def test_disk_space_check_accepts_paths_with_headroom(tmp_path):
    _write_fake_df(tmp_path, size=10_000, free=5_000, inodes=1_000, inode_free=500)
    path = tmp_path / "data"
    path.mkdir()

    r = subprocess.run([str(DISK_CHECK), "--path", str(path), "--min-free-mib", "0",
                        "--dump-size-dir", str(tmp_path / "no-dumps"),
                        "--min-free-percent", "10", "--min-inode-free-percent", "5"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"path={path}" in r.stdout
    assert "free_bytes=5000" in r.stdout
    assert "free_percent=50" in r.stdout
    assert "inode_free_percent=50" in r.stdout
    assert "status=ok" in r.stdout


def test_disk_space_check_rejects_low_free_bytes(tmp_path):
    _write_fake_df(tmp_path, size=10_000, free=512, inodes=1_000, inode_free=500)
    path = tmp_path / "data"
    path.mkdir()

    r = subprocess.run([str(DISK_CHECK), "--path", str(path), "--min-free-mib", "1",
                        "--dump-size-dir", str(tmp_path / "no-dumps"),
                        "--min-free-percent", "1", "--min-inode-free-percent", "1"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "Low disk free bytes" in r.stderr


def test_disk_space_check_rejects_low_free_percent(tmp_path):
    _write_fake_df(tmp_path, size=10_000, free=900, inodes=1_000, inode_free=500)
    path = tmp_path / "data"
    path.mkdir()

    r = subprocess.run([str(DISK_CHECK), "--path", str(path), "--min-free-mib", "0",
                        "--dump-size-dir", str(tmp_path / "no-dumps"),
                        "--min-free-percent", "10", "--min-inode-free-percent", "1"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "Low disk free percent" in r.stderr


def test_disk_space_check_rejects_low_inode_percent(tmp_path):
    _write_fake_df(tmp_path, size=10_000, free=5_000, inodes=1_000, inode_free=40)
    path = tmp_path / "data"
    path.mkdir()

    r = subprocess.run([str(DISK_CHECK), "--path", str(path), "--min-free-mib", "0",
                        "--dump-size-dir", str(tmp_path / "no-dumps"),
                        "--min-free-percent", "1", "--min-inode-free-percent", "5"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "Low inode free percent" in r.stderr


def test_disk_space_check_rejects_missing_path(tmp_path):
    _write_fake_df(tmp_path)
    missing = tmp_path / "missing"

    r = subprocess.run([str(DISK_CHECK), "--path", str(missing), "--min-free-mib", "0",
                        "--dump-size-dir", str(tmp_path / "no-dumps")],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "not found or not a directory" in r.stderr


def test_disk_space_check_rejects_unsafe_backup_directory_permissions(tmp_path):
    _write_fake_df(tmp_path)
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    backup_dir.chmod(0o755)

    r = subprocess.run([str(DISK_CHECK), "--path", str(backup_dir), "--min-free-mib", "0",
                        "--dump-size-dir", str(tmp_path / "no-dumps"),
                        "--require-private-dirs"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe monitored directory permissions" in r.stderr


def test_backup_restore_drill_accepts_latest_dump_without_database_url(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf '; archive created at test\\n1; 0 0 TABLE public alerts postgres\\n'
""")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"latest_dump={dump}" in r.stdout
    assert "checksum=ok" in r.stdout
    assert "pg_restore_list=ok" in r.stdout
    assert "restore_entries=2" in r.stdout
    assert "--list" in calls.read_text()


def test_backup_restore_drill_scrubs_inherited_database_env(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf 'DATABASE_URL=%s\\nPGPASSWORD=%s\\nPGHOST=%s\\nPGUSER=%s\\n' "$DATABASE_URL" "$PGPASSWORD" "$PGHOST" "$PGUSER" > "{calls}"
printf 'entry\\n'
""")

    r = subprocess.run(
        [str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
        env=_env(
            tmp_path,
            DATABASE_URL="postgresql://user:secret@db.example.com/cwwd",
            PGPASSWORD="secret",
            PGHOST="db.example.com",
            PGUSER="user",
        ),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    assert "pg_restore_list=ok" in r.stdout
    assert "secret" not in r.stdout + r.stderr + calls.read_text()
    assert calls.read_text() == "DATABASE_URL=\nPGPASSWORD=\nPGHOST=\nPGUSER=\n"


def test_backup_restore_drill_rejects_missing_dump(tmp_path):
    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "No cwwd-YYYYMMDDTHHMMSSZ.dump files found" in r.stderr


def test_backup_restore_drill_rejects_checksum_mismatch_before_pg_restore(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    dump.write_text("tampered")
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nexit 0\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 1
    assert "FAILED" in r.stdout
    assert "computed checksum did NOT match" in r.stderr


def test_backup_restore_drill_rejects_stale_latest_dump(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nprintf 'entry\\n'\n")
    old_time = time.time() - (3 * 60 * 60)
    os.utime(dump, (old_time, old_time))
    os.utime(Path(f"{dump}.sha256"), (old_time, old_time))

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path),
                        "--warn-age-hours", "1", "--max-age-hours", "2"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "Latest backup restore drill target is stale" in r.stderr
    assert "max_age_seconds=7200" in r.stderr


def test_backup_restore_drill_rejects_unsafe_file_permissions(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    dump.chmod(0o644)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nprintf 'entry\\n'\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr


def test_backup_restore_drill_rejects_manifest_target_mismatch(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    Path(f"{dump}.sha256").write_text("hash  other.dump\n")
    Path(f"{dump}.sha256").chmod(0o600)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nprintf 'entry\\n'\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest target mismatch" in r.stderr


def test_backup_restore_drill_rejects_orphan_manifest(tmp_path):
    manifest = tmp_path / "cwwd-20260713T000000Z.dump.sha256"
    manifest.write_text("hash  cwwd-20260713T000000Z.dump\n")
    manifest.chmod(0o600)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nprintf 'entry\\n'\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest is missing dump" in r.stderr


def test_backup_restore_drill_rejects_stale_tmp_file(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    tmp = tmp_path / "cwwd-20260713T010000Z.dump.tmp"
    tmp.write_text("partial")
    tmp.chmod(0o600)
    old_time = time.time() - (2 * 60 * 60)
    os.utime(tmp, (old_time, old_time))
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nprintf 'entry\\n'\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Stale temporary backup file present" in r.stderr


def test_backup_restore_drill_rejects_empty_pg_restore_list(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nexit 0\n")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "pg_restore --list returned no entries" in r.stderr


def test_backup_restore_drill_redacts_pg_restore_stderr(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    _write_exe(tmp_path / "pg_restore", """#!/usr/bin/env bash
echo 'failed postgresql://user:secret@db.example.com/cwwd' >&2
exit 9
""")

    r = subprocess.run([str(RESTORE_DRILL), "--backup-dir", str(tmp_path), "--max-age-hours", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 9
    assert "postgresql://***:***@db.example.com/cwwd" in r.stderr
    assert "secret" not in r.stderr


def test_ops_alert_requires_title_and_message():
    r = subprocess.run([str(OPS_ALERT), "--dry-run"], text=True, capture_output=True)

    assert r.returncode == 2
    assert "--title is required" in r.stderr


def test_ops_alert_dry_run_without_webhooks_logs_no_targets():
    r = subprocess.run([str(OPS_ALERT), "--title", "Backup failed", "--message", "Check journal",
                        "--severity", "alert", "--dry-run"],
                       env=_env(Path("/tmp")), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "journald=skipped" in r.stdout
    assert "webhook_targets=0" in r.stdout


def test_ops_alert_env_file_is_whitelisted_and_not_printed(tmp_path):
    env_file = tmp_path / "ops-alert.env"
    env_file.write_text(
        "SLACK_WEBHOOK_URL=https://hooks.example/slack-secret\n"
        "TEAMS_WEBHOOK_URL=https://hooks.example/teams-secret\n"
        "DATABASE_URL=postgresql://should:not@appear/db\n"
        "JWT_SECRET=should-not-appear\n"
    )
    env_file.chmod(0o600)

    r = subprocess.run([str(OPS_ALERT), "--env-file", str(env_file), "--title", "Backup failed",
                        "--message", "Check journal", "--dry-run"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "webhook_targets=2" in r.stdout
    combined = r.stdout + r.stderr
    assert "slack-secret" not in combined
    assert "teams-secret" not in combined
    assert "should-not-appear" not in combined
    assert "postgresql://" not in combined


def test_ops_alert_rejects_unsafe_env_file_permissions(tmp_path):
    env_file = tmp_path / "ops-alert.env"
    env_file.write_text("SLACK_WEBHOOK_URL=https://hooks.example/slack-secret\n")
    env_file.chmod(0o644)

    r = subprocess.run([str(OPS_ALERT), "--env-file", str(env_file), "--title", "Backup failed",
                        "--message", "Check journal", "--dry-run"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe alert env file permissions" in r.stderr
    assert "slack-secret" not in r.stderr


def _write_fake_systemctl(tmp_path: Path, *,
                          inactive: str = "",
                          disabled: str = "",
                          failed: str = "") -> None:
    _write_exe(tmp_path / "systemctl", f"""#!/usr/bin/env bash
cmd="$1"
shift
inactive="{inactive}"
disabled="{disabled}"
failed="{failed}"
if [[ "$cmd" == "show" ]]; then
  unit="$1"
  prop=""
  for arg in "$@"; do
    case "$arg" in
      --property=*)
        prop="${{arg#--property=}}"
        ;;
    esac
  done
  if [[ "$prop" == "ActiveState" ]]; then
    if [[ "$unit" == "$inactive" ]]; then
      printf 'inactive\\n'
    else
      printf 'active\\n'
    fi
    exit 0
  fi
  if [[ "$prop" == "UnitFileState" ]]; then
    if [[ "$unit" == "$disabled" ]]; then
      printf 'disabled\\n'
    else
      printf 'enabled\\n'
    fi
    exit 0
  fi
  if [[ "$prop" == "Result" ]]; then
    printf 'success\\n'
    exit 0
  fi
  if [[ "$prop" == "NRestarts" ]]; then
    printf '0\\n'
    exit 0
  fi
  if [[ "$prop" == "LastTriggerUSec" ]]; then
    printf 'Mon 2026-07-13 14:00:00 JST\\n'
    exit 0
  fi
  if [[ "$prop" == "NextElapseUSecRealtime" ]]; then
    printf 'Mon 2026-07-13 14:30:00 JST\\n'
    exit 0
  fi
fi
if [[ "$cmd" == "list-units" ]]; then
  if [[ -n "$failed" ]]; then
    printf '%s loaded failed failed test failure\\n' "$failed"
  fi
  exit 0
fi
printf 'unexpected systemctl call: %s %s\\n' "$cmd" "$*" >&2
exit 9
""")


def test_ops_status_accepts_all_required_units(tmp_path):
    _write_fake_systemctl(tmp_path)

    r = subprocess.run([str(OPS_STATUS)], env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "cwwd-backend.service.ActiveState=active" in r.stdout
    assert "cwwd-app-health-check.timer.UnitFileState=enabled" in r.stdout
    assert "cwwd-app-health-check.timer.LastTriggerUSec=Mon 2026-07-13 14:00:00 JST" in r.stdout
    assert "cwwd-app-health-check.service.Result=success" in r.stdout
    assert "failed_units=0" in r.stdout
    assert "status=ok" in r.stdout


def test_ops_status_does_not_print_secret_environment(tmp_path):
    _write_fake_systemctl(tmp_path)

    r = subprocess.run(
        [str(OPS_STATUS)],
        env=_env(
            tmp_path,
            DATABASE_URL="postgresql://user:do-not-print@db.example.com/cwwd",
            SLACK_WEBHOOK_URL="https://hooks.example/do-not-print",
            PASSWORD="do-not-print",
            TOKEN="do-not-print",
        ),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "postgresql://" not in combined
    assert "hooks.example" not in combined
    assert "DATABASE_URL" not in combined
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "do-not-print" not in combined


def test_ops_status_json_accepts_all_required_units(tmp_path):
    _write_fake_systemctl(tmp_path)

    r = subprocess.run([str(OPS_STATUS), "--json"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["status"] == "ok"
    assert payload["failed_units_count"] == 0
    assert payload["failed_units"] == []
    assert payload["services"][0]["unit"] == "cwwd-backend.service"
    assert payload["services"][0]["active_state"] == "active"
    assert payload["timers"][0]["unit"] == "cwwd-cloudflared-config-check.timer"
    assert payload["timers"][0]["unit_file_state"] == "enabled"
    assert any(timer["unit"] == "cwwd-systemd-timer-freshness-check.timer"
               for timer in payload["timers"])


def test_ops_status_json_reports_failed_units_without_secrets(tmp_path):
    _write_fake_systemctl(tmp_path, failed="cwwd-app-health-check.service")

    r = subprocess.run(
        [str(OPS_STATUS), "--format", "json", "--allow-failed-units"],
        env=_env(
            tmp_path,
            DATABASE_URL="postgresql://user:do-not-print@db.example.com/cwwd",
            SLACK_WEBHOOK_URL="https://hooks.example/do-not-print",
            PASSWORD="do-not-print",
            TOKEN="do-not-print",
        ),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["status"] == "ok"
    assert payload["failed_units_count"] == 1
    assert payload["failed_units"] == ["cwwd-app-health-check.service"]
    combined = r.stdout + r.stderr
    assert "postgresql://" not in combined
    assert "hooks.example" not in combined
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "do-not-print" not in combined


def test_ops_status_json_rejects_inactive_required_service_with_parseable_error(tmp_path):
    _write_fake_systemctl(tmp_path, inactive="cwwd-tunnel.service")

    r = subprocess.run([str(OPS_STATUS), "--json"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    payload = json.loads(r.stdout)
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "message": "cwwd-tunnel.service ActiveState mismatch",
        "unit": "cwwd-tunnel.service",
        "property": "ActiveState",
        "actual": "inactive",
        "expected": "active",
    }
    assert "cwwd-tunnel.service ActiveState mismatch" in r.stderr


def test_ops_status_json_export_writes_valid_snapshot_without_printing_body(tmp_path):
    status_script = tmp_path / "ops-status"
    output = tmp_path / "state" / "ops-status.json"
    _write_exe(status_script, """#!/usr/bin/env bash
printf '{"status":"ok","secret":"do-not-print-body"}\\n'
""")

    r = subprocess.run(
        [str(OPS_STATUS_JSON_EXPORT), "--status-script", str(status_script), "--output", str(output)],
        env=_env(tmp_path, PASSWORD="do-not-print-env"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    payload = json.loads(output.read_text())
    assert payload["status"] == "ok"
    assert payload["secret"] == "do-not-print-body"
    assert "ops_status_json=" in r.stdout
    assert "bytes=" in r.stdout
    assert "status=ok" in r.stdout
    combined = r.stdout + r.stderr
    assert "do-not-print-body" not in combined
    assert "do-not-print-env" not in combined
    assert oct(output.stat().st_mode & 0o777) == "0o640"


def test_ops_status_json_export_writes_failure_snapshot_and_returns_status_code(tmp_path):
    status_script = tmp_path / "ops-status"
    output = tmp_path / "state" / "ops-status.json"
    _write_exe(status_script, """#!/usr/bin/env bash
printf '{"status":"failed","failed_units":["cwwd-app-health-check.service"]}\\n'
exit 3
""")

    r = subprocess.run(
        [str(OPS_STATUS_JSON_EXPORT), "--status-script", str(status_script), "--output", str(output)],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    payload = json.loads(output.read_text())
    assert payload["status"] == "failed"
    assert payload["failed_units"] == ["cwwd-app-health-check.service"]
    assert "ops_status_exit_code=3" in r.stdout
    assert "ops status reported failure" in r.stderr


def _write_ops_status_snapshot(path: Path, *, status: str = "ok", failed_units_count: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o750)
    path.write_text(json.dumps({
        "snapshot_utc": "2026-07-13T06:00:00Z",
        "services": [{"unit": "cwwd-backend.service", "active_state": "active"}],
        "timers": [{"unit": "cwwd-ops-status.timer", "active_state": "active"}],
        "failed_units": [],
        "failed_units_count": failed_units_count,
        "status": status,
    }))
    path.chmod(0o640)


def test_ops_status_json_check_accepts_fresh_valid_snapshot(tmp_path):
    snapshot = tmp_path / "state" / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    owner = snapshot.stat().st_uid
    user = os.environ.get("USER", "kensan")
    group = subprocess.run(["stat", "-c", "%G", str(snapshot)],
                           text=True, capture_output=True, check=True).stdout.strip()
    assert owner >= 0

    r = subprocess.run(
        [str(OPS_STATUS_JSON_CHECK), "--path", str(snapshot), "--owner", user, "--group", group,
         "--max-age-minutes", "60"],
        env=_env(tmp_path, PASSWORD="do-not-print"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    assert "snapshot_status=ok" in r.stdout
    assert "failed_units_count=0" in r.stdout
    assert "services=1" in r.stdout
    assert "timers=1" in r.stdout
    assert "status=ok" in r.stdout
    combined = r.stdout + r.stderr
    assert "do-not-print" not in combined


def test_ops_status_json_check_rejects_failed_snapshot_without_printing_body(tmp_path):
    snapshot = tmp_path / "state" / "ops-status.json"
    _write_ops_status_snapshot(snapshot, status="failed", failed_units_count=1)
    user = os.environ.get("USER", "kensan")
    group = subprocess.run(["stat", "-c", "%G", str(snapshot)],
                           text=True, capture_output=True, check=True).stdout.strip()

    r = subprocess.run(
        [str(OPS_STATUS_JSON_CHECK), "--path", str(snapshot), "--owner", user, "--group", group],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "ops_status_json_status_mismatch" in r.stderr
    combined = r.stdout + r.stderr
    assert '"status": "failed"' not in combined


def test_ops_status_json_check_rejects_stale_snapshot(tmp_path):
    snapshot = tmp_path / "state" / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    old = int(time.time()) - 7200
    os.utime(snapshot, (old, old))
    user = os.environ.get("USER", "kensan")
    group = subprocess.run(["stat", "-c", "%G", str(snapshot)],
                           text=True, capture_output=True, check=True).stdout.strip()

    r = subprocess.run(
        [str(OPS_STATUS_JSON_CHECK), "--path", str(snapshot), "--owner", user, "--group", group,
         "--max-age-minutes", "60"],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "ops_status_json_stale" in r.stderr


def test_ops_status_json_check_rejects_invalid_json_without_printing_body(tmp_path):
    snapshot = tmp_path / "state" / "ops-status.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.parent.chmod(0o750)
    snapshot.write_text('{"status":"ok","secret":"do-not-print"')
    snapshot.chmod(0o640)
    user = os.environ.get("USER", "kensan")
    group = subprocess.run(["stat", "-c", "%G", str(snapshot)],
                           text=True, capture_output=True, check=True).stdout.strip()

    r = subprocess.run(
        [str(OPS_STATUS_JSON_CHECK), "--path", str(snapshot), "--owner", user, "--group", group],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "ops_status_json_invalid" in r.stderr
    combined = r.stdout + r.stderr
    assert "do-not-print" not in combined
    assert "secret" not in combined


def test_ops_status_json_check_rejects_unsafe_permissions(tmp_path):
    snapshot = tmp_path / "state" / "ops-status.json"
    _write_ops_status_snapshot(snapshot)
    snapshot.chmod(0o644)
    user = os.environ.get("USER", "kensan")
    group = subprocess.run(["stat", "-c", "%G", str(snapshot)],
                           text=True, capture_output=True, check=True).stdout.strip()

    r = subprocess.run(
        [str(OPS_STATUS_JSON_CHECK), "--path", str(snapshot), "--owner", user, "--group", group],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "ops_status_json_mode_mismatch" in r.stderr


def test_ops_status_rejects_inactive_required_service(tmp_path):
    _write_fake_systemctl(tmp_path, inactive="cwwd-tunnel.service")

    r = subprocess.run([str(OPS_STATUS)], env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "cwwd-tunnel.service ActiveState mismatch" in r.stderr


def test_ops_status_rejects_disabled_required_timer(tmp_path):
    _write_fake_systemctl(tmp_path, disabled="cwwd-app-health-check.timer")

    r = subprocess.run([str(OPS_STATUS)], env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "cwwd-app-health-check.timer UnitFileState mismatch" in r.stderr


def test_ops_status_rejects_failed_cwwd_units(tmp_path):
    _write_fake_systemctl(tmp_path, failed="cwwd-app-health-check.service")

    r = subprocess.run([str(OPS_STATUS)], env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "failed_units=1" in r.stdout
    assert "failed_unit=cwwd-app-health-check.service" in r.stdout
    assert "Failed cwwd systemd units are present" in r.stderr


def test_ops_status_can_report_failed_units_without_failing(tmp_path):
    _write_fake_systemctl(tmp_path, failed="cwwd-app-health-check.service")

    r = subprocess.run([str(OPS_STATUS), "--allow-failed-units"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "failed_units=1" in r.stdout
    assert "status=ok" in r.stdout


def _write_fake_timer_freshness_systemctl(tmp_path: Path, *,
                                          stale: str = "",
                                          untriggered: str = "",
                                          disabled: str = "",
                                          inactive: str = "",
                                          next_far: str = "") -> None:
    _write_exe(tmp_path / "systemctl", f"""#!/usr/bin/env bash
cmd="$1"
shift
stale="{stale}"
untriggered="{untriggered}"
disabled="{disabled}"
inactive="{inactive}"
next_far="{next_far}"
if [[ "$cmd" == "show" ]]; then
  unit="$1"
  prop=""
  for arg in "$@"; do
    case "$arg" in
      --property=*)
        prop="${{arg#--property=}}"
        ;;
    esac
  done
  if [[ "$prop" == "ActiveState" ]]; then
    if [[ "$unit" == "$inactive" ]]; then
      printf 'inactive\\n'
    else
      printf 'active\\n'
    fi
    exit 0
  fi
  if [[ "$prop" == "UnitFileState" ]]; then
    if [[ "$unit" == "$disabled" ]]; then
      printf 'disabled\\n'
    else
      printf 'enabled\\n'
    fi
    exit 0
  fi
  if [[ "$prop" == "LastTriggerUSec" ]]; then
    if [[ "$unit" == "$untriggered" ]]; then
      printf '\\n'
    elif [[ "$unit" == "$stale" ]]; then
      printf 'Mon 2026-07-13 12:00:00 JST\\n'
    else
      printf 'Mon 2026-07-13 14:00:00 JST\\n'
    fi
    exit 0
  fi
  if [[ "$prop" == "NextElapseUSecRealtime" ]]; then
    if [[ "$unit" == "$next_far" ]]; then
      printf 'Mon 2026-07-13 18:00:00 JST\\n'
    else
      printf 'Mon 2026-07-13 14:05:00 JST\\n'
    fi
    exit 0
  fi
fi
printf 'unexpected systemctl call: %s %s\\n' "$cmd" "$*" >&2
exit 9
""")


def test_timer_freshness_accepts_recent_and_scheduled_timers(tmp_path):
    _write_fake_timer_freshness_systemctl(tmp_path, untriggered="cwwd-db-backup.timer")

    r = subprocess.run(
        [
            str(TIMER_FRESHNESS),
            "--now-epoch", "1783918860",
            "--timer", "cwwd-app-health-check.timer:900",
            "--timer", "cwwd-db-backup.timer:172800",
        ],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    assert "timer=cwwd-app-health-check.timer age_seconds=60" in r.stdout
    assert "timer=cwwd-db-backup.timer last_trigger=not-yet" in r.stdout
    assert "timers_checked=2" in r.stdout
    assert "status=ok" in r.stdout


def test_timer_freshness_rejects_stale_timer_without_printing_secrets(tmp_path):
    _write_fake_timer_freshness_systemctl(tmp_path, stale="cwwd-app-health-check.timer")

    r = subprocess.run(
        [str(TIMER_FRESHNESS), "--now-epoch", "1783918860",
         "--timer", "cwwd-app-health-check.timer:900"],
        env=_env(
            tmp_path,
            DATABASE_URL="postgresql://user:do-not-print@db.example.com/cwwd",
            SLACK_WEBHOOK_URL="https://hooks.example/do-not-print",
        ),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "timer_stale=cwwd-app-health-check.timer" in r.stderr
    assert "systemd timer freshness check failed" in r.stderr
    combined = r.stdout + r.stderr
    assert "postgresql://" not in combined
    assert "hooks.example" not in combined
    assert "do-not-print" not in combined


def test_timer_freshness_rejects_disabled_timer(tmp_path):
    _write_fake_timer_freshness_systemctl(tmp_path, disabled="cwwd-app-health-check.timer")

    r = subprocess.run(
        [str(TIMER_FRESHNESS), "--now-epoch", "1783918860",
         "--timer", "cwwd-app-health-check.timer:900"],
        env=_env(tmp_path),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    assert "cwwd-app-health-check.timer UnitFileState mismatch" in r.stderr


def _write_fake_failed_report_tools(tmp_path: Path, *,
                                    failed: str = "",
                                    journal: str = "service failed\n") -> None:
    _write_fake_systemctl(tmp_path, failed=failed)
    _write_exe(tmp_path / "journalctl", f"""#!/usr/bin/env bash
unit=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -u)
      unit="$2"
      shift 2
      ;;
    -n|--output)
      shift 2
      ;;
    --no-pager)
      shift
      ;;
    *)
      shift
      ;;
  esac
done
if [[ -n "$unit" ]]; then
  printf '%s' {journal!r}
fi
""")


def test_ops_failed_units_report_accepts_no_failed_units(tmp_path):
    _write_fake_failed_report_tools(tmp_path)

    r = subprocess.run([str(OPS_FAILED_REPORT)], env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "failed_units=0" in r.stdout
    assert "status=ok" in r.stdout


def test_ops_failed_units_report_prints_failed_unit_details_and_fails(tmp_path):
    _write_fake_failed_report_tools(tmp_path, failed="cwwd-app-health-check.service")

    r = subprocess.run([str(OPS_FAILED_REPORT), "--lines", "2"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "failed_units=1" in r.stdout
    assert "failed_unit=cwwd-app-health-check.service" in r.stdout
    assert "cwwd-app-health-check.service.ActiveState=active" in r.stdout
    assert "journal[cwwd-app-health-check.service]=service failed" in r.stdout
    assert "Failed cwwd systemd units are present" in r.stderr


def test_ops_failed_units_report_allow_failed_units_returns_zero(tmp_path):
    _write_fake_failed_report_tools(tmp_path, failed="cwwd-app-health-check.service")

    r = subprocess.run([str(OPS_FAILED_REPORT), "--allow-failed-units"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "failed_units=1" in r.stdout
    assert "status=reported" in r.stdout


def test_ops_failed_units_report_redacts_secret_logs_and_environment(tmp_path):
    _write_fake_failed_report_tools(
        tmp_path,
        failed="cwwd-app-health-check.service",
        journal=(
            "DATABASE_URL=postgresql://user:secret@db.example.com/cwwd "
            "Authorization Bearer token-secret PASSWORD=secret "
            "https://hooks.example/secret\n"
        ),
    )

    r = subprocess.run(
        [str(OPS_FAILED_REPORT), "--allow-failed-units"],
        env=_env(tmp_path, PASSWORD="secret", TOKEN="secret"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "DATABASE_URL=***" in combined
    assert "Bearer ***" in combined
    assert "PASSWORD=***" in combined
    assert "https://***" in combined
    assert "token-secret" not in combined
    assert "hooks.example" not in combined
    assert "postgresql://user" not in combined
    assert "PASSWORD=secret" not in combined


def _write_cloudflared_config(tmp_path: Path, *, body: str | None = None) -> tuple[Path, Path]:
    credential = tmp_path / "tunnel.json"
    credential.write_text('{"secret":"do-not-print"}\n')
    credential.chmod(0o600)
    config = tmp_path / "config-cwwd.yml"
    if body is None:
        body = f"""
tunnel: test-tunnel-id
credentials-file: {credential}
ingress:
  - hostname: cwwd.example.com
    path: ^/api(/.*)?$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/health$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/readyz$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/docs$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/docs/.*$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/redoc$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/openapi\\.json$
    service: http_status:404
  - hostname: cwwd.example.com
    service: http://localhost:34979
  - service: http_status:404
"""
    config.write_text(body)
    return config, credential


def _cloudflared_check_cmd(config: Path) -> list[str]:
    return [
        str(CLOUDFLARED_CONFIG_CHECK),
        "--config", str(config),
        "--hostname", "cwwd.example.com",
        "--tunnel-id", "test-tunnel-id",
        "--backend-port", "55019",
        "--frontend-port", "34979",
    ]


def test_cloudflared_config_check_accepts_expected_ingress(tmp_path):
    config, _credential = _write_cloudflared_config(tmp_path)

    r = subprocess.run(_cloudflared_check_cmd(config), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "ingress_rules=9" in r.stdout
    assert "backend_paths=3" in r.stdout
    assert "edge_404_paths=4" in r.stdout
    assert "credentials_file=present" in r.stdout
    assert "status=ok" in r.stdout


def test_cloudflared_config_check_rejects_missing_docs_404(tmp_path):
    config, _credential = _write_cloudflared_config(tmp_path)
    config.write_text(config.read_text().replace(
        "  - hostname: cwwd.example.com\n    path: ^/redoc$\n    service: http_status:404\n",
        "",
    ))

    r = subprocess.run(_cloudflared_check_cmd(config), text=True, capture_output=True)

    assert r.returncode == 3
    assert "edge_404_paths_missing" in r.stderr


def test_cloudflared_config_check_rejects_backend_after_frontend(tmp_path):
    credential = tmp_path / "tunnel.json"
    credential.write_text("{}")
    config, _credential = _write_cloudflared_config(tmp_path, body=f"""
tunnel: test-tunnel-id
credentials-file: {credential}
ingress:
  - hostname: cwwd.example.com
    service: http://localhost:34979
  - hostname: cwwd.example.com
    path: ^/api(/.*)?$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/health$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/readyz$
    service: http://localhost:55019
  - hostname: cwwd.example.com
    path: ^/docs$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/docs/.*$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/redoc$
    service: http_status:404
  - hostname: cwwd.example.com
    path: ^/openapi\\.json$
    service: http_status:404
  - service: http_status:404
""")

    r = subprocess.run(_cloudflared_check_cmd(config), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_paths_after_frontend" in r.stderr


def test_cloudflared_config_check_rejects_regex_overmatch(tmp_path):
    config, _credential = _write_cloudflared_config(tmp_path)
    config.write_text(config.read_text().replace("^/health$", "^/health"))

    r = subprocess.run(_cloudflared_check_cmd(config), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_paths_missing" in r.stderr or "path_regex_overmatch=/healthz" in r.stderr


def test_cloudflared_config_check_rejects_missing_credential_file(tmp_path):
    config, credential = _write_cloudflared_config(tmp_path)
    credential.unlink()

    r = subprocess.run(_cloudflared_check_cmd(config), text=True, capture_output=True)

    assert r.returncode == 3
    assert "credentials_file_not_found" in r.stderr


def test_cloudflared_config_check_does_not_print_secret_environment_or_credential_contents(tmp_path):
    config, _credential = _write_cloudflared_config(tmp_path)
    config.write_text(config.read_text().replace("test-tunnel-id", "other-tunnel-id"))

    r = subprocess.run(
        _cloudflared_check_cmd(config),
        env=_env(tmp_path, PASSWORD="do-not-print", TOKEN="do-not-print"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "do-not-print" not in combined
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined


def _write_fake_health_curl(tmp_path: Path, *, ready_status: str = "200",
                            public_status: str = "302", frontend_header: bool = True,
                            frontend_api_status: str = "401") -> None:
    header_lines = (
        "HTTP/1.1 200 OK\\r\\n"
        "X-Content-Type-Options: nosniff\\r\\n"
        "X-Frame-Options: DENY\\r\\n"
        "Cache-Control: no-store\\r\\n"
        "Content-Security-Policy-Report-Only: default-src 'self'\\r\\n"
        "\\r\\n"
    )
    frontend_headers = header_lines if frontend_header else "HTTP/1.1 200 OK\\r\\n\\r\\n"
    _write_exe(tmp_path / "curl", f"""#!/usr/bin/env bash
out=""
headers=""
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out="$2"
      shift 2
      ;;
    --dump-header)
      headers="$2"
      shift 2
      ;;
    --write-out|--connect-timeout|--max-time|--max-redirs)
      shift 2
      ;;
    --silent|--show-error|--location-trusted)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
case "$url" in
  *"/health")
    printf '{{"status":"ok","app":"Civil-Weather-Water-Decision","env":"production"}}' > "$out"
    printf "{header_lines}" > "$headers"
    printf '200'
    ;;
  *"/readyz")
    printf '{{"status":"ok","checks":{{"database":true,"migrations":true,"tables":true,"config":true}}}}' > "$out"
    printf "{header_lines}" > "$headers"
    printf '{ready_status}'
    ;;
  "http://127.0.0.1:34979/api/auth/me"*)
    printf '{{"detail":"Not authenticated"}}' > "$out"
    printf "{header_lines}" > "$headers"
    printf '{frontend_api_status}'
    ;;
  "http://127.0.0.1:34979/"*)
    printf '<html>ok</html>' > "$out"
    printf "{frontend_headers}" > "$headers"
    printf '200'
    ;;
  "none")
    printf 'unexpected none url' >&2
    exit 9
    ;;
  *)
    printf '' > "$out"
    printf 'HTTP/1.1 {public_status} Found\\r\\nLocation: https://cwwd.cloudflareaccess.com/login\\r\\n\\r\\n' > "$headers"
    printf '{public_status}'
    ;;
esac
""")


def test_app_health_check_accepts_local_services_and_cloudflare_access_edge(tmp_path):
    _write_fake_health_curl(tmp_path)

    r = subprocess.run([str(APP_HEALTH), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "backend_health=ok" in r.stdout
    assert "backend_readyz=ok" in r.stdout
    assert "frontend=ok" in r.stdout
    assert "frontend_api_proxy=ok" in r.stdout
    assert "public_edge=ok" in r.stdout
    assert "public_status=302" in r.stdout


def test_app_health_check_can_skip_public_edge(tmp_path):
    _write_fake_health_curl(tmp_path)

    r = subprocess.run([str(APP_HEALTH), "--public-url", "none", "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "public_edge=skipped" in r.stdout


def test_app_health_check_rejects_not_ready_backend(tmp_path):
    _write_fake_health_curl(tmp_path, ready_status="503")

    r = subprocess.run([str(APP_HEALTH), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_readyz status mismatch" in r.stderr


def test_app_health_check_rejects_frontend_missing_security_header(tmp_path):
    _write_fake_health_curl(tmp_path, frontend_header=False)

    r = subprocess.run([str(APP_HEALTH), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "frontend missing expected header pattern" in r.stderr


def test_app_health_check_rejects_broken_frontend_api_proxy(tmp_path):
    _write_fake_health_curl(tmp_path, frontend_api_status="502")

    r = subprocess.run([str(APP_HEALTH), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "frontend_api_proxy status mismatch" in r.stderr


def test_app_health_check_rejects_unexpected_public_status(tmp_path):
    _write_fake_health_curl(tmp_path, public_status="200")

    r = subprocess.run([str(APP_HEALTH), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "public_edge status mismatch" in r.stderr


def _write_fake_public_edge_curl(tmp_path: Path, *, status: str = "302",
                                 location: bool = True,
                                 location_url: str = "https://cwwd.cloudflareaccess.com/cdn-cgi/access/login") -> None:
    location_header = f"Location: {location_url}\\r\\n" if location else ""
    _write_exe(tmp_path / "curl", f"""#!/usr/bin/env bash
out=""
headers=""
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out="$2"
      shift 2
      ;;
    --dump-header)
      headers="$2"
      shift 2
      ;;
    --write-out|--connect-timeout|--max-time|--max-redirs)
      shift 2
      ;;
    --silent|--show-error)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf 'secret do-not-print body for %s' "$url" > "$out"
printf 'HTTP/1.1 {status} Found\\r\\n{location_header}\\r\\n' > "$headers"
printf '{status}'
""")


def test_public_edge_access_check_accepts_all_default_paths(tmp_path):
    _write_fake_public_edge_curl(tmp_path)

    r = subprocess.run([str(PUBLIC_EDGE), "--base-url", "https://cwwd.example.com",
                        "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "public_edge_path=/ status=302 access=ok" in r.stdout
    assert "public_edge_path=/api/sites status=302 access=ok" in r.stdout
    assert "public_edge_path=/health status=302 access=ok" in r.stdout
    assert "public_edge_path=/readyz status=302 access=ok" in r.stdout
    assert "public_edge_path=/docs status=302 access=ok" in r.stdout
    assert "public_edge_path=/openapi.json status=302 access=ok" in r.stdout
    assert "paths_checked=6" in r.stdout
    assert "status=ok" in r.stdout


def test_public_edge_access_check_rejects_origin_passthrough_status(tmp_path):
    _write_fake_public_edge_curl(tmp_path, status="200")

    r = subprocess.run([str(PUBLIC_EDGE), "--base-url", "https://cwwd.example.com",
                        "--path", "/api/sites", "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "public_edge path=/api/sites status mismatch" in r.stderr


def test_public_edge_access_check_rejects_redirect_without_location(tmp_path):
    _write_fake_public_edge_curl(tmp_path, status="302", location=False)

    r = subprocess.run([str(PUBLIC_EDGE), "--base-url", "https://cwwd.example.com",
                        "--path", "/docs", "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "public_edge path=/docs missing expected header pattern" in r.stderr


def test_public_edge_access_check_rejects_non_access_redirect(tmp_path):
    _write_fake_public_edge_curl(
        tmp_path,
        status="302",
        location_url="https://cwwd.example.com/login",
    )

    r = subprocess.run([str(PUBLIC_EDGE), "--base-url", "https://cwwd.example.com",
                        "--path", "/openapi.json", "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "public_edge path=/openapi.json location missing expected substring" in r.stderr
    assert "https://cwwd.example.com/login" not in r.stderr


def test_public_edge_access_check_does_not_print_response_body_or_secret_env(tmp_path):
    _write_fake_public_edge_curl(tmp_path, status="200")

    r = subprocess.run(
        [str(PUBLIC_EDGE), "--base-url", "https://cwwd.example.com", "--path", "/health",
         "--timeout-seconds", "3"],
        env=_env(tmp_path, PASSWORD="do-not-print", TOKEN="do-not-print"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "do-not-print" not in combined
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined


def _write_fake_security_curl(tmp_path: Path, *, docs_status: str = "404",
                              auth_status: str = "401", backend_headers: bool = True,
                              frontend_csp: bool = True) -> None:
    backend_header_lines = (
        "HTTP/1.1 200 OK\\r\\n"
        "X-Content-Type-Options: nosniff\\r\\n"
        "X-Frame-Options: DENY\\r\\n"
        "Referrer-Policy: no-referrer\\r\\n"
        "Permissions-Policy: geolocation=(), microphone=(), camera=()\\r\\n"
        "Strict-Transport-Security: max-age=31536000\\r\\n"
        "Cross-Origin-Opener-Policy: same-origin\\r\\n"
        "Cross-Origin-Resource-Policy: same-site\\r\\n"
        "X-Permitted-Cross-Domain-Policies: none\\r\\n"
        "X-Download-Options: noopen\\r\\n"
        "Cache-Control: no-store\\r\\n"
        "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\\r\\n"
        "\\r\\n"
    )
    missing_backend_headers = "HTTP/1.1 200 OK\\r\\n\\r\\n"
    frontend_header_lines = (
        "HTTP/1.1 200 OK\\r\\n"
        "X-Content-Type-Options: nosniff\\r\\n"
        "X-Frame-Options: DENY\\r\\n"
        "Referrer-Policy: no-referrer\\r\\n"
        "Permissions-Policy: geolocation=(), microphone=(), camera=()\\r\\n"
        "Strict-Transport-Security: max-age=31536000\\r\\n"
        "Cross-Origin-Opener-Policy: same-origin\\r\\n"
        "Cross-Origin-Resource-Policy: same-site\\r\\n"
        "X-Permitted-Cross-Domain-Policies: none\\r\\n"
        "X-Download-Options: noopen\\r\\n"
        "Cache-Control: no-store\\r\\n"
        "Content-Security-Policy-Report-Only: default-src 'self'; frame-ancestors 'none'; connect-src 'self' http://127.0.0.1:* http://localhost:* http://[::1]:*\\r\\n"
        "\\r\\n"
    )
    frontend_missing_csp = frontend_header_lines.replace(
        "Content-Security-Policy-Report-Only: default-src 'self'; frame-ancestors 'none'; connect-src 'self' http://127.0.0.1:* http://localhost:* http://[::1]:*\\r\\n",
        "",
    )
    backend_headers_text = backend_header_lines if backend_headers else missing_backend_headers
    frontend_headers_text = frontend_header_lines if frontend_csp else frontend_missing_csp
    _write_exe(tmp_path / "curl", f"""#!/usr/bin/env bash
out=""
headers=""
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out="$2"
      shift 2
      ;;
    --dump-header)
      headers="$2"
      shift 2
      ;;
    --write-out|--connect-timeout|--max-time|--max-redirs)
      shift 2
      ;;
    --silent|--show-error)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
case "$url" in
  *"/health")
    printf '{{"status":"ok","secret":"do-not-print"}}' > "$out"
    printf "{backend_headers_text}" > "$headers"
    printf '200'
    ;;
  *"/api/sites")
    printf '{{"detail":"secret do-not-print"}}' > "$out"
    printf "{backend_headers_text}" > "$headers"
    printf '{auth_status}'
    ;;
  *"/docs"|*"/redoc"|*"/openapi.json")
    printf '{{"detail":"secret do-not-print"}}' > "$out"
    printf "{backend_headers_text}" > "$headers"
    printf '{docs_status}'
    ;;
  "http://127.0.0.1:34979/"*)
    printf '<html>secret do-not-print</html>' > "$out"
    printf "{frontend_headers_text}" > "$headers"
    printf '200'
    ;;
  *)
    printf 'unexpected url: %s\\n' "$url" >&2
    exit 9
    ;;
esac
""")


def test_security_surface_check_accepts_expected_production_surface(tmp_path):
    _write_fake_security_curl(tmp_path)

    r = subprocess.run([str(SECURITY_SURFACE), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "backend_health_security=ok" in r.stdout
    assert "backend_auth_guard=ok" in r.stdout
    assert "backend_docs_disabled=ok" in r.stdout
    assert "frontend_security=ok" in r.stdout
    assert "status=ok" in r.stdout


def test_security_surface_check_rejects_enabled_docs(tmp_path):
    _write_fake_security_curl(tmp_path, docs_status="200")

    r = subprocess.run([str(SECURITY_SURFACE), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_docs_disabled /docs status mismatch" in r.stderr


def test_security_surface_check_rejects_unprotected_api(tmp_path):
    _write_fake_security_curl(tmp_path, auth_status="200")

    r = subprocess.run([str(SECURITY_SURFACE), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_auth_guard status mismatch" in r.stderr


def test_security_surface_check_rejects_backend_missing_security_header(tmp_path):
    _write_fake_security_curl(tmp_path, backend_headers=False)

    r = subprocess.run([str(SECURITY_SURFACE), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "backend_health_security missing expected header pattern" in r.stderr


def test_security_surface_check_rejects_frontend_missing_report_only_csp(tmp_path):
    _write_fake_security_curl(tmp_path, frontend_csp=False)

    r = subprocess.run([str(SECURITY_SURFACE), "--timeout-seconds", "3"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 3
    assert "frontend_security missing expected header pattern" in r.stderr


def test_security_surface_check_does_not_print_response_bodies_or_secret_env(tmp_path):
    _write_fake_security_curl(tmp_path, auth_status="200")

    r = subprocess.run(
        [str(SECURITY_SURFACE), "--timeout-seconds", "3"],
        env=_env(tmp_path, PASSWORD="secret", TOKEN="secret"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "do-not-print" not in combined
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "secret" not in combined


def _ipv4_proc_hex(address: str) -> str:
    return "".join(f"{int(part):02X}" for part in reversed(address.split(".")))


def _ipv6_proc_hex(address: str) -> str:
    import ipaddress

    packed = ipaddress.IPv6Address(address).packed
    return "".join(packed[index:index + 4][::-1].hex().upper() for index in range(0, 16, 4))


def _tcp_line(address_hex: str, port: int, state: str = "0A") -> str:
    return (
        f"   0: {address_hex}:{port:04X} 00000000:0000 {state} "
        "00000000:00000000 00:00000000 00000000 1000 0 0 1 1 0000000000000000 100 0 0 10 0\n"
    )


def _tcp_listen_line(address_hex: str, port: int) -> str:
    return _tcp_line(address_hex, port, "0A")


def _write_proc_tcp_files(tmp_path: Path, *, tcp_lines: list[str],
                          tcp6_lines: list[str] | None = None) -> tuple[Path, Path]:
    tcp = tmp_path / "tcp"
    tcp6 = tmp_path / "tcp6"
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
    tcp.write_text(header + "".join(tcp_lines))
    tcp6.write_text(header + "".join(tcp6_lines or []))
    return tcp, tcp6


def test_network_exposure_check_accepts_loopback_listeners(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 55019),
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 34979),
        ],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "port_55019_listeners=127.0.0.1" in r.stdout
    assert "port_34979_exposure=ok" in r.stdout
    assert "status=ok" in r.stdout


def test_network_exposure_check_accepts_ipv6_loopback_listener(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[_tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 55019)],
        tcp6_lines=[_tcp_listen_line(_ipv6_proc_hex("::1"), 34979)],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "port_34979_listeners=::1" in r.stdout
    assert "status=ok" in r.stdout


def test_network_exposure_check_rejects_wildcard_listener(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[
            _tcp_listen_line(_ipv4_proc_hex("0.0.0.0"), 55019),
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 34979),
        ],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "port_55019_non_loopback_listener=0.0.0.0" in r.stderr


def test_network_exposure_check_rejects_lan_listener(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[
            _tcp_listen_line(_ipv4_proc_hex("192.168.0.185"), 55019),
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 34979),
        ],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "port_55019_non_loopback_listener=192.168.0.185" in r.stderr


def test_network_exposure_check_rejects_ipv6_wildcard_listener(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[_tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 34979)],
        tcp6_lines=[_tcp_listen_line(_ipv6_proc_hex("::"), 55019)],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "port_55019_non_loopback_listener=::" in r.stderr


def test_network_exposure_check_ignores_non_listen_states_and_unrelated_ports(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[
            _tcp_line(_ipv4_proc_hex("0.0.0.0"), 55019, "01"),
            _tcp_listen_line(_ipv4_proc_hex("0.0.0.0"), 5432),
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 55019),
            _tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 34979),
        ],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "0.0.0.0" not in r.stdout + r.stderr
    assert "status=ok" in r.stdout


def test_network_exposure_check_rejects_missing_required_port(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[_tcp_listen_line(_ipv4_proc_hex("127.0.0.1"), 55019)],
    )

    r = subprocess.run([str(NETWORK_EXPOSURE), "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "port_34979_listeners=missing" in r.stderr


def test_network_exposure_check_does_not_print_secret_environment(tmp_path):
    tcp, tcp6 = _write_proc_tcp_files(
        tmp_path,
        tcp_lines=[_tcp_listen_line(_ipv4_proc_hex("0.0.0.0"), 55019)],
    )

    r = subprocess.run(
        [str(NETWORK_EXPOSURE), "--port", "55019", "--tcp-file", str(tcp), "--tcp6-file", str(tcp6)],
        env=_env(tmp_path, PASSWORD="secret", TOKEN="secret"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "secret" not in combined


def _write_unit_pair(tmp_path: Path, unit: str, repo_body: str = "[Service]\nType=oneshot\n",
                     system_body: str | None = None) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    system_dir = tmp_path / "system"
    repo_dir.mkdir(exist_ok=True)
    system_dir.mkdir(exist_ok=True)
    (repo_dir / unit).write_text(repo_body)
    (system_dir / unit).write_text(repo_body if system_body is None else system_body)
    return repo_dir, system_dir


def test_systemd_unit_drift_check_accepts_matching_units(tmp_path):
    repo_dir, system_dir = _write_unit_pair(tmp_path, "cwwd-test.service")
    _write_unit_pair(tmp_path, "cwwd-test.timer", "[Timer]\nOnUnitActiveSec=1h\n")

    r = subprocess.run([str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "repo_units=2" in r.stdout
    assert "units_checked=2" in r.stdout
    assert "status=ok" in r.stdout


def test_systemd_unit_drift_check_rejects_missing_system_unit(tmp_path):
    repo_dir, system_dir = _write_unit_pair(tmp_path, "cwwd-test.service")
    (system_dir / "cwwd-test.service").unlink()

    r = subprocess.run([str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "missing_system_unit=cwwd-test.service" in r.stderr
    assert "systemd unit drift detected" in r.stderr


def test_systemd_unit_drift_check_rejects_changed_system_unit_without_printing_content(tmp_path):
    repo_dir, system_dir = _write_unit_pair(
        tmp_path,
        "cwwd-test.service",
        repo_body="[Service]\nExecStart=/bin/true\n",
        system_body="[Service]\nExecStart=/bin/false # secret do-not-print\n",
    )

    r = subprocess.run([str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "unit_drift=cwwd-test.service" in r.stderr
    assert "do-not-print" not in r.stdout + r.stderr


def test_systemd_unit_drift_check_rejects_extra_system_unit_unless_allowed(tmp_path):
    repo_dir, system_dir = _write_unit_pair(tmp_path, "cwwd-test.service")
    (system_dir / "cwwd-extra.service").write_text("[Service]\nType=oneshot\n")

    r = subprocess.run([str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "extra_system_unit=cwwd-extra.service" in r.stderr

    allowed = subprocess.run([str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir),
                              "--allow-extra-units"],
                             text=True, capture_output=True)
    assert allowed.returncode == 0, allowed.stderr
    assert "extra_units=0" in allowed.stdout


def test_systemd_unit_drift_check_does_not_print_secret_environment(tmp_path):
    repo_dir, system_dir = _write_unit_pair(
        tmp_path,
        "cwwd-test.service",
        repo_body="[Service]\nExecStart=/bin/true\n",
        system_body="[Service]\nExecStart=/bin/false\n",
    )

    r = subprocess.run(
        [str(SYSTEMD_DRIFT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)],
        env=_env(tmp_path, PASSWORD="secret", TOKEN="secret"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "secret" not in combined


def test_secret_file_permission_check_accepts_private_required_and_optional_files(tmp_path):
    required = tmp_path / "required.env"
    optional = tmp_path / "ops-alert.env"
    required.write_text("DATABASE_URL=postgresql://do-not-print:do-not-print@example/db\n")
    optional.write_text("SLACK_WEBHOOK_URL=https://hooks.example/do-not-print\n")
    required.chmod(0o600)
    optional.chmod(0o400)
    owner = required.owner()
    group = required.group()

    r = subprocess.run([str(SECRET_PERMS), "--owner", owner, "--group", group,
                        "--required-file", str(required), "--optional-file", str(optional)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"file_ok={required}" in r.stdout
    assert f"file_ok={optional}" in r.stdout
    assert "files_checked=2" in r.stdout
    assert "status=ok" in r.stdout
    assert "do-not-print" not in r.stdout + r.stderr


def test_secret_file_permission_check_accepts_missing_optional_file(tmp_path):
    required = tmp_path / "required.env"
    optional = tmp_path / "missing.env"
    required.write_text("secret")
    required.chmod(0o600)
    owner = required.owner()
    group = required.group()

    r = subprocess.run([str(SECRET_PERMS), "--owner", owner, "--group", group,
                        "--required-file", str(required), "--optional-file", str(optional)],
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"optional_file_missing={optional}" in r.stdout
    assert "optional_missing=1" in r.stdout
    assert "status=ok" in r.stdout


def test_secret_file_permission_check_rejects_missing_required_file(tmp_path):
    missing = tmp_path / "missing.env"

    r = subprocess.run([str(SECRET_PERMS), "--required-file", str(missing)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert f"missing_required_file={missing}" in r.stderr
    assert "Secret/config file permission check failed" in r.stderr


def test_secret_file_permission_check_rejects_unsafe_mode_without_reading_content(tmp_path):
    secret_file = tmp_path / "required.env"
    secret_file.write_text("PASSWORD=do-not-print\n")
    secret_file.chmod(0o644)
    owner = secret_file.owner()
    group = secret_file.group()

    r = subprocess.run([str(SECRET_PERMS), "--owner", owner, "--group", group,
                        "--required-file", str(secret_file)],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "unsafe_file_permissions" in r.stderr
    assert "mode=644" in r.stderr
    assert "do-not-print" not in r.stdout + r.stderr


def test_secret_file_permission_check_rejects_owner_group_mismatch(tmp_path):
    secret_file = tmp_path / "required.env"
    secret_file.write_text("do-not-print")
    secret_file.chmod(0o600)
    owner = secret_file.owner()
    group = secret_file.group()

    r_owner = subprocess.run([str(SECRET_PERMS), "--owner", "definitely-not-the-owner",
                              "--group", group, "--required-file", str(secret_file)],
                             text=True, capture_output=True)
    r_group = subprocess.run([str(SECRET_PERMS), "--owner", owner,
                              "--group", "definitely-not-the-group",
                              "--required-file", str(secret_file)],
                             text=True, capture_output=True)

    assert r_owner.returncode == 3
    assert "owner_mismatch" in r_owner.stderr
    assert r_group.returncode == 3
    assert "group_mismatch" in r_group.stderr


def test_secret_file_permission_check_does_not_print_secret_environment(tmp_path):
    secret_file = tmp_path / "required.env"
    secret_file.write_text("secret")
    secret_file.chmod(0o644)
    owner = secret_file.owner()
    group = secret_file.group()

    r = subprocess.run(
        [str(SECRET_PERMS), "--owner", owner, "--group", group, "--required-file", str(secret_file)],
        env=_env(tmp_path, PASSWORD="do-not-print", TOKEN="do-not-print"),
        text=True,
        capture_output=True,
    )

    assert r.returncode == 3
    combined = r.stdout + r.stderr
    assert "PASSWORD" not in combined
    assert "TOKEN" not in combined
    assert "do-not-print" not in combined


def _write_fake_gpg(tmp_path: Path) -> Path:
    calls = tmp_path / "gpg.calls"
    _write_exe(tmp_path / "gpg", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
out=""
in=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out="$2"
      shift 2
      ;;
    --passphrase-file)
      shift 2
      ;;
    --*)
      shift
      ;;
    *)
      in="$1"
      shift
      ;;
  esac
done
cp "$in" "$out"
""")
    return calls


def test_backup_export_requires_private_passphrase_file(tmp_path):
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump)
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o644)

    r = subprocess.run([str(EXPORT), "--backup-dir", str(tmp_path),
                        "--output-dir", str(tmp_path / "exports"),
                        "--passphrase-file", str(passphrase)],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr
    assert "secret-passphrase" not in r.stderr


def test_backup_export_creates_encrypted_archive_and_checksum_without_secret_argv(tmp_path):
    calls = _write_fake_gpg(tmp_path)
    backup_dir = tmp_path / "postgres"
    export_dir = tmp_path / "exports"
    backup_dir.mkdir()
    backup_dir.chmod(0o700)
    dump = backup_dir / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump, "dump-data")
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)

    r = subprocess.run([str(EXPORT), "--backup-dir", str(backup_dir),
                        "--output-dir", str(export_dir),
                        "--passphrase-file", str(passphrase),
                        "--retention-days", "30"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    exported = export_dir / "cwwd-20260713T000000Z.dump.tar.gpg"
    assert exported.exists()
    assert Path(f"{exported}.sha256").exists()
    assert "export=" in r.stdout
    assert "sha256=" in r.stdout
    assert "secret-passphrase" not in r.stdout + r.stderr + calls.read_text()
    assert "--passphrase-file" in calls.read_text()
    assert "--no-random-seed-file" in calls.read_text()
    assert f"  {exported.name}" in Path(f"{exported}.sha256").read_text()
    assert oct(exported.stat().st_mode & 0o777) == "0o600"
    assert oct(Path(f"{exported}.sha256").stat().st_mode & 0o777) == "0o600"


def test_backup_export_rejects_checksum_mismatch_before_gpg(tmp_path):
    calls = _write_fake_gpg(tmp_path)
    dump = tmp_path / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump, "dump-data")
    dump.write_text("tampered")
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)

    r = subprocess.run([str(EXPORT), "--backup-dir", str(tmp_path),
                        "--output-dir", str(tmp_path / "exports"),
                        "--passphrase-file", str(passphrase)],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 1
    assert "FAILED" in r.stdout
    assert not calls.exists()


def test_backup_export_retention_prunes_only_old_exports_after_success(tmp_path):
    _write_fake_gpg(tmp_path)
    backup_dir = tmp_path / "postgres"
    export_dir = tmp_path / "exports"
    backup_dir.mkdir()
    export_dir.mkdir()
    backup_dir.chmod(0o700)
    export_dir.chmod(0o700)
    dump = backup_dir / "cwwd-20260713T000000Z.dump"
    _write_dump_with_checksum(dump, "dump-data")
    old_export = export_dir / "cwwd-20000101T000000Z.dump.tar.gpg"
    old_manifest = export_dir / "cwwd-20000101T000000Z.dump.tar.gpg.sha256"
    unrelated = export_dir / "manual-note.txt"
    old_export.write_text("old")
    old_manifest.write_text("old-hash  cwwd-20000101T000000Z.dump.tar.gpg\n")
    unrelated.write_text("keep")
    old_export.chmod(0o600)
    old_manifest.chmod(0o600)
    old_time = time.time() - (3 * 24 * 60 * 60)
    os.utime(old_export, (old_time, old_time))
    os.utime(old_manifest, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)

    r = subprocess.run([str(EXPORT), "--backup-dir", str(backup_dir),
                        "--output-dir", str(export_dir),
                        "--passphrase-file", str(passphrase),
                        "--retention-days", "1"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "export_retention_deleted=2" in r.stdout
    assert not old_export.exists()
    assert not old_manifest.exists()
    assert unrelated.exists()


def _write_fake_decrypt_gpg(tmp_path: Path, tar_path: Path) -> Path:
    calls = tmp_path / "gpg-decrypt.calls"
    _write_exe(tmp_path / "gpg", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      out="$2"
      shift 2
      ;;
    --passphrase-file)
      shift 2
      ;;
    --*)
      shift
      ;;
    *)
      shift
      ;;
  esac
done
cp "{tar_path}" "$out"
""")
    return calls


def _write_export_tar(tmp_path: Path, name: str, entries: dict[str, str] | None = None) -> Path:
    tar_path = tmp_path / f"{name}.tar"
    dump_name = f"{name}.dump"
    default_entries = {
        dump_name: "dump-data",
        f"{dump_name}.sha256": f"hash  {dump_name}\n",
    }
    with tarfile.open(tar_path, "w") as tar:
        for entry_name, body in (entries or default_entries).items():
            item = tmp_path / entry_name.replace("/", "_")
            item.write_text(body)
            tar.add(item, arcname=entry_name)
    return tar_path


def test_backup_export_check_accepts_fresh_export_with_checksum_and_decrypt_list(tmp_path):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    export_dir.chmod(0o700)
    archive = export_dir / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive, "encrypted-data")
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)
    calls = _write_fake_decrypt_gpg(tmp_path, _write_export_tar(tmp_path, "cwwd-20260713T000000Z"))

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(export_dir),
                        "--max-age-hours", "28", "--passphrase-file", str(passphrase)],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert f"latest_export={archive}" in r.stdout
    assert "checksum=ok" in r.stdout
    assert "decrypt_list=ok" in r.stdout
    assert "secret-passphrase" not in r.stdout + r.stderr + calls.read_text()
    assert "--passphrase-file" in calls.read_text()
    assert "--no-random-seed-file" in calls.read_text()


def test_backup_export_check_rejects_missing_export(tmp_path):
    tmp_path.chmod(0o700)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "No cwwd-YYYYMMDDTHHMMSSZ.dump.tar.gpg files found" in r.stderr


def test_backup_export_check_rejects_missing_checksum(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    archive.write_text("encrypted-data")
    archive.chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "missing checksum manifest" in r.stderr


def test_backup_export_check_rejects_checksum_mismatch(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive, "encrypted-data")
    archive.write_text("tampered")

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 1
    assert "FAILED" in r.stdout
    assert "computed checksum did NOT match" in r.stderr


def test_backup_export_check_rejects_stale_latest_export(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    old_time = time.time() - (3 * 60 * 60)
    os.utime(archive, (old_time, old_time))
    os.utime(Path(f"{archive}.sha256"), (old_time, old_time))

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path),
                        "--warn-age-hours", "1", "--max-age-hours", "2"],
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "Latest encrypted export is stale" in r.stderr
    assert "max_age_seconds=7200" in r.stderr


def test_backup_export_check_rejects_orphan_manifest(tmp_path):
    tmp_path.chmod(0o700)
    manifest = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg.sha256"
    manifest.write_text("hash  cwwd-20260713T000000Z.dump.tar.gpg\n")
    manifest.chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest is missing encrypted export" in r.stderr


def test_backup_export_check_rejects_manifest_target_mismatch(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    Path(f"{archive}.sha256").write_text("hash  other.dump.tar.gpg\n")
    Path(f"{archive}.sha256").chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Checksum manifest target mismatch" in r.stderr


def test_backup_export_check_rejects_zero_byte_export(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    archive.write_text("")
    archive.chmod(0o600)
    Path(f"{archive}.sha256").write_text("hash  cwwd-20260713T000000Z.dump.tar.gpg\n")
    Path(f"{archive}.sha256").chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Zero-byte encrypted export" in r.stderr


def test_backup_export_check_rejects_stale_tmp_file(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    partial = tmp_path / "cwwd-20260713T010000Z.dump.tar.gpg.tmp"
    partial.write_text("partial")
    partial.chmod(0o600)
    old_time = time.time() - (2 * 60 * 60)
    os.utime(partial, (old_time, old_time))

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Stale temporary encrypted export file present" in r.stderr


def test_backup_export_check_rejects_malformed_export_name(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-latest.dump.tar.gpg"
    archive.write_text("encrypted")
    archive.chmod(0o600)
    Path(f"{archive}.sha256").write_text("hash  cwwd-latest.dump.tar.gpg\n")
    Path(f"{archive}.sha256").chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Malformed encrypted export filename" in r.stderr


def test_backup_export_check_rejects_unsafe_permissions_without_secret_leak(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    archive.chmod(0o644)
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path),
                        "--passphrase-file", str(passphrase), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr
    assert "secret-passphrase" not in r.stderr + r.stdout


def test_backup_export_check_rejects_unsafe_passphrase_permissions_without_secret_leak(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o644)

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path),
                        "--passphrase-file", str(passphrase), "--max-age-hours", "28"],
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Unsafe file permissions" in r.stderr
    assert "secret-passphrase" not in r.stderr + r.stdout


def test_backup_export_check_rejects_decrypted_tar_with_extra_or_missing_entries(tmp_path):
    tmp_path.chmod(0o700)
    archive = tmp_path / "cwwd-20260713T000000Z.dump.tar.gpg"
    _write_export_with_checksum(archive)
    passphrase = tmp_path / "backup-export.passphrase"
    passphrase.write_text("secret-passphrase")
    passphrase.chmod(0o600)
    _write_fake_decrypt_gpg(
        tmp_path,
        _write_export_tar(tmp_path, "cwwd-20260713T000000Z", {
            "cwwd-20260713T000000Z.dump": "dump-data",
            "cwwd-20260713T000000Z.dump.sha256": "hash  cwwd-20260713T000000Z.dump\n",
            "extra.txt": "extra",
        }),
    )

    r = subprocess.run([str(EXPORT_CHECK), "--export-dir", str(tmp_path),
                        "--passphrase-file", str(passphrase), "--max-age-hours", "28"],
                       env=_env(tmp_path), text=True, capture_output=True)

    assert r.returncode == 2
    assert "unexpected entry count" in r.stderr


def test_restore_rejects_without_confirmation(tmp_path):
    dump = tmp_path / "cwwd.dump"
    _write_dump_with_checksum(dump)
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nexit 0\n")

    r = subprocess.run([str(RESTORE), "--dump", str(dump)],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 3
    assert "Refusing destructive restore" in r.stderr


def test_restore_dry_run_only_lists_dump(tmp_path):
    dump = tmp_path / "cwwd.dump"
    _write_dump_with_checksum(dump)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(dump), "--dry-run"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "dry_run=ok" in r.stdout
    assert "--list" in calls.read_text()
    assert "--clean" not in calls.read_text()


def test_restore_confirmed_path_is_transactional_and_stops_on_error(tmp_path):
    dump = tmp_path / "cwwd.dump"
    _write_dump_with_checksum(dump)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(dump)],
                       env=_env(
                           tmp_path,
                           DATABASE_URL="postgresql://u:p@db.example.com/cwwd",
                           CWWD_RESTORE_CONFIRM="RESTORE",
                       ),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    restore_args = calls.read_text()
    assert "--single-transaction" in restore_args
    assert "--exit-on-error" in restore_args
    assert "--clean" in restore_args
    assert "--dbname=cwwd" in restore_args
    assert "postgresql://" not in restore_args


def test_restore_requires_checksum_manifest_by_default(tmp_path):
    dump = tmp_path / "cwwd.dump"
    dump.write_text("dump")
    _write_exe(tmp_path / "pg_restore", "#!/usr/bin/env bash\nexit 0\n")

    r = subprocess.run([str(RESTORE), "--dump", str(dump), "--dry-run"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 2
    assert "Missing checksum manifest" in r.stderr


def test_restore_allows_missing_checksum_only_with_explicit_override(tmp_path):
    dump = tmp_path / "cwwd.dump"
    dump.write_text("dump")
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(dump), "--dry-run", "--allow-missing-checksum"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "allow-missing-checksum" in r.stderr
    assert "--list" in calls.read_text()


def test_restore_rejects_checksum_mismatch_before_pg_restore(tmp_path):
    dump = tmp_path / "cwwd.dump"
    _write_dump_with_checksum(dump)
    dump.write_text("tampered")
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(dump), "--dry-run"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 1
    assert "FAILED" in r.stdout
    assert "computed checksum did NOT match" in r.stderr
    assert not calls.exists()


def test_restore_checksum_manifest_survives_moving_dump_directory(tmp_path):
    source_dir = tmp_path / "source"
    moved_dir = tmp_path / "moved"
    source_dir.mkdir()
    dump = source_dir / "cwwd.dump"
    _write_dump_with_checksum(dump)
    source_dir.rename(moved_dir)
    moved_dump = moved_dir / "cwwd.dump"
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(moved_dump), "--dry-run"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "--list" in calls.read_text()


def test_restore_accepts_legacy_manifest_path_written_from_repo_root(tmp_path):
    dump = tmp_path / "cwwd.dump"
    dump.write_text("dump")
    digest = subprocess.run(["sha256sum", str(dump)], text=True, capture_output=True, check=True).stdout
    Path(f"{dump}.sha256").write_text(digest)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
exit 0
""")

    r = subprocess.run([str(RESTORE), "--dump", str(dump), "--dry-run"],
                       env=_env(tmp_path, DATABASE_URL="postgresql://u:p@db.example.com/cwwd"),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    assert "--list" in calls.read_text()


def test_restore_env_file_scrubs_inherited_database_url_direct(tmp_path):
    dump = tmp_path / "cwwd.dump"
    _write_dump_with_checksum(dump)
    calls = tmp_path / "pg_restore.calls"
    _write_exe(tmp_path / "pg_restore", f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "{calls}"
printf 'PGHOST=%s\\nPGDATABASE=%s\\nPGUSER=%s\\n' "$PGHOST" "$PGDATABASE" "$PGUSER" >> "{calls}"
exit 0
""")
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://env-file:pw@db.example.com/cwwd\n")

    r = subprocess.run([str(RESTORE), "--env-file", str(env_file), "--dump", str(dump)],
                       env=_env(
                           tmp_path,
                           DATABASE_URL_DIRECT="postgresql://stale:pw@ep-test-pooler.neon.tech/cwwd",
                           CWWD_RESTORE_CONFIRM="RESTORE",
                       ),
                       text=True, capture_output=True)

    assert r.returncode == 0, r.stderr
    call_text = calls.read_text()
    assert "PGUSER=env-file" in call_text
    assert "PGHOST=db.example.com" in call_text
    assert "stale:pw@ep-test-pooler" not in call_text
    assert "postgresql://" not in call_text
