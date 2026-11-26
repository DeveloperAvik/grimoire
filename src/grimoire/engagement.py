"""Local engagement state: banner acceptance, scope and audit log.

Everything lives under GRIMOIRE_HOME (or the user config dir). Only non-secret
metadata is stored; never put credentials in the scope fields.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def base_dir() -> Path:
    env = os.environ.get("GRIMOIRE_HOME")
    if env:
        path = Path(env)
    else:
        try:
            from platformdirs import user_config_dir

            path = Path(user_config_dir("grimoire"))
        except ImportError:
            path = Path.home() / ".config" / "grimoire"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def has_accepted() -> bool:
    data = _read_json(base_dir() / "accepted.json")
    return bool(data and data.get("accepted") is True)


def record_acceptance() -> None:
    _write_json(base_dir() / "accepted.json", {"accepted": True, "ts": _now()})


def load_scope() -> dict | None:
    data = _read_json(base_dir() / "scope.json")
    if data and data.get("authorization_ref") and data.get("targets"):
        return data
    return None


def save_scope(authorization_ref: str, targets: str) -> dict:
    scope = {"authorization_ref": authorization_ref, "targets": targets, "ts": _now()}
    _write_json(base_dir() / "scope.json", scope)
    return scope


def append_audit(record: dict) -> None:
    path = base_dir() / "audit.jsonl"
    new = not path.exists()
    with path.open("a") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    if new:
        path.chmod(0o600)


def now() -> str:
    return _now()
