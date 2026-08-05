"""systemd-unit-drift-check の ignore-missing-units オプション回帰テスト。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "scripts" / "systemd-unit-drift-check.sh"


def _run(repo_dir: Path, system_dir: Path, ignore: str = "") -> subprocess.CompletedProcess:
    cmd = [str(SCRIPT), "--repo-dir", str(repo_dir), "--system-dir", str(system_dir)]
    if ignore:
        cmd += ["--ignore-missing-units", ignore]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_ignore_missing_units_skips_only_listed(tmp_path):
    repo = tmp_path / "repo"
    system = tmp_path / "system"
    repo.mkdir()
    system.mkdir()
    (repo / "cwwd-a.service").write_text("[Service]\n")
    (repo / "cwwd-b.service").write_text("[Service]\n")
    (repo / "cwwd-c.timer").write_text("[Timer]\n")
    (system / "cwwd-a.service").write_text("[Service]\n")

    # b/c がignore対象なら成功、ignoreしないと失敗
    ok = _run(repo, system, "cwwd-b.service,cwwd-c.timer")
    assert ok.returncode == 0, ok.stderr
    assert "skipped_missing_unit=cwwd-b.service" in ok.stderr
    assert "status=ok" in ok.stdout

    ng = _run(repo, system, "cwwd-b.service")
    assert ng.returncode != 0
    assert "missing_system_unit=cwwd-c.timer" in ng.stderr


def test_drift_detects_changed_unit(tmp_path):
    repo = tmp_path / "repo"
    system = tmp_path / "system"
    repo.mkdir()
    system.mkdir()
    (repo / "cwwd-a.service").write_text("[Service]\nExecStart=/bin/true\n")
    (system / "cwwd-a.service").write_text("[Service]\nExecStart=/bin/false\n")

    r = _run(repo, system)
    assert r.returncode != 0
    assert "unit_drift=cwwd-a.service" in r.stderr


if __name__ == "__main__":
    sys.exit(0)
