"""Backup, restore, and upgrade helpers (Gate 44).

Backups carry user DATA (database file + selected workspace/config
directories), never binaries. Restores are selective by table group so
an operator can recover memory without touching tasks. Upgrades run
migrations and preserve every user-data table; uninstall keeps data
unless the operator explicitly opts into removal (see installer.iss).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional


USER_TABLES = ["tasks", "contexts", "events", "memories", "skills", "agents",
               "devices", "verifications", "personalities", "preferences",
               "system_snapshots", "usage_events", "usage_patterns",
               "learning_proposals", "agent_definitions", "agent_tasks",
               "agent_messages", "workspaces", "compute_jobs", "device_presence",
               "audit_log", "learning_policy", "scheduler_jobs"]


def backup_user_data(db_path: str, out_path: str,
                     extra_dirs: Optional[list[str]] = None) -> str:
    """Copy the database file + metadata manifest into a backup path."""
    import zipfile

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"db": Path(db_path).name, "extra_dirs": extra_dirs or []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(db_path, manifest["db"])
        for extra in extra_dirs or []:
            root = Path(extra)
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, f"extra/{root.name}/{path.name}")
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return str(out)


def restore_user_data(backup_path: str, db_path: str,
                      tables: Optional[list[str]] = None) -> str:
    """Restore the database file (full) or selected tables only."""
    import zipfile

    with zipfile.ZipFile(backup_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if tables is None:
            archive.extract(manifest["db"], Path(db_path).parent)
            extracted = Path(db_path).parent / manifest["db"]
            if str(extracted) != str(db_path):
                shutil.move(str(extracted), db_path)
            return db_path
        # Selective restore: copy table rows from a temp extraction.
        tmp = Path(db_path).parent / "_restore_tmp.db"
        with zipfile.ZipFile(backup_path) as second:
            second.extract(manifest["db"], tmp.parent)
        try:
            origin = sqlite3.connect(str(tmp.parent / manifest["db"]))
            target = sqlite3.connect(db_path)
            try:
                for table in tables:
                    if table not in USER_TABLES:
                        raise ValueError(f"unknown table {table!r}")
                    rows = origin.execute(
                        f"SELECT id, snapshot, updated_at FROM {table}").fetchall()
                    for row in rows:
                        target.execute(
                            f"INSERT OR REPLACE INTO {table} "
                            "(id, snapshot, updated_at) VALUES (?, ?, ?)", row)
                target.commit()
            finally:
                target.close()
                origin.close()
        finally:
            tmp.unlink(missing_ok=True)
            extracted = tmp.parent / manifest["db"]
            if extracted.exists():
                extracted.unlink()
        return db_path


def compare_versions(left: str, right: str) -> int:
    """Semantic comparison: -1 / 0 / +1 (unknown chunks compare as text)."""
    def parts(version: str) -> list:
        return [int(c) if c.isdigit() else c for c in version.split(".")]

    return (parts(left) > parts(right)) - (parts(left) < parts(right))


def plan_upgrade(installed: str, incoming: str) -> dict:
    """Decide upgrade/downgrade/same (downgrades need explicit force)."""
    comparison = compare_versions(installed, incoming)
    if comparison == 0:
        return {"action": "same", "migrate": False, "preserve_data": True}
    if comparison < 0:
        return {"action": "upgrade", "migrate": True, "preserve_data": True}
    return {"action": "downgrade", "migrate": False, "preserve_data": True,
            "requires_force": True}
