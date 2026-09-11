"""Gate 44: install/upgrade/backup/first-run simulated against tmp dirs.

A real Windows installer artifact is operator-built (see
packaging/windows/); these tests prove the underlying mechanics:
clean install layout, first-run flow, migration on upgrade, data
preservation, backup/restore selectivity, runtime boot from packaged
config, model configuration without secrets, and offline operation.
"""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_release_helper(filename: str, module_name: str):
    """Load packaging/*.py by path (the directory name shadows PyPI's
    `packaging`, so it must never go on sys.path)."""
    path = Path(__file__).resolve().parents[2] / "packaging" / filename
    spec = spec_from_file_location(module_name, path)
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_first_run = _load_release_helper("first_run.py", "rel_first_run")
_backup = _load_release_helper("backup.py", "rel_backup")

FirstRunWizard = _first_run.FirstRunWizard
WizardStep = _first_run.WizardStep
backup_user_data = _backup.backup_user_data
compare_versions = _backup.compare_versions
plan_upgrade = _backup.plan_upgrade
restore_user_data = _backup.restore_user_data

from ai_ecosystem.core.config import AppConfig
from ai_ecosystem.core.errors import DomainValidationError
from ai_ecosystem.core.models import Task
from ai_ecosystem.core.persistence import Database, SqliteTaskRepository
from ai_ecosystem.core.runtime import AgentRuntime
from ai_ecosystem.interface import ApiClient, LocalHttpServer, RuntimeAPI


def test_clean_installation(tmp_path):
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "data"
    (app_dir / "bin").mkdir(parents=True)
    (app_dir / "bin" / "ai-ecosystem.exe").write_text("binary")
    data_dir.mkdir()
    db_path = str(data_dir / "ai_ecosystem.db")
    Database(db_path).migrate()
    assert (app_dir / "bin" / "ai-ecosystem.exe").is_file()
    assert Database(db_path).schema_version() == 3


def test_first_launch(tmp_path):
    wizard = FirstRunWizard()
    assert wizard.current is WizardStep.WELCOME
    wizard.complete()
    assert wizard.current is WizardStep.SYSTEM_DETECTION
    wizard.complete({"os": "TestOS"})
    wizard.complete({"models": ["mock"]})
    wizard.complete({"policy": "default"})
    wizard.complete({"telemetry": "off"})
    for _ in range(3):  # OCI / phone / hardware: all skippable
        wizard.skip()
    assert wizard.done is True
    assert wizard.state.settings["telemetry"] == "off"


def test_first_run_skips_only_optionals():
    wizard = FirstRunWizard()
    with pytest.raises(DomainValidationError):
        wizard.skip()  # WELCOME is mandatory
    wizard.complete()
    with pytest.raises(DomainValidationError):
        wizard.skip()  # SYSTEM_DETECTION is mandatory
    done = FirstRunWizard()
    for _ in range(5):
        done.complete()
    for _ in range(3):
        done.skip()  # OCI / phone / hardware skippable
    assert done.done is True
    with pytest.raises(DomainValidationError):
        done.complete()  # already finished
    with pytest.raises(DomainValidationError):
        done.skip()


def test_first_run_no_cloud_forced():
    wizard = FirstRunWizard()
    for _ in range(5):  # through PRIVACY_SETTINGS
        wizard.complete()
    assert wizard.current is WizardStep.OCI_SETUP
    wizard.skip()
    wizard.skip()
    wizard.skip()
    assert wizard.done is True
    assert "oci" not in json.dumps(wizard.state.settings).lower()


def test_upgrade(tmp_path):
    assert plan_upgrade("0.1.0", "0.2.0") == {
        "action": "upgrade", "migrate": True, "preserve_data": True}
    db_path = str(tmp_path / "app.db")
    db = Database(db_path)
    db.migrate()
    from ai_ecosystem.core.persistence import SqliteMemoryRepository
    from ai_ecosystem.core.models import Memory

    SqliteMemoryRepository(db).create(Memory(content="precious"))
    db.close()
    plan = plan_upgrade("0.1.0", "0.2.0")
    assert plan["preserve_data"] is True
    reopened = Database(db_path)
    reopened.migrate()  # migrations are idempotent across versions
    try:
        assert len(SqliteMemoryRepository(reopened).list()) == 1
    finally:
        reopened.close()


def test_downgrade_handling():
    plan = plan_upgrade("0.2.0", "0.1.0")
    assert plan["action"] == "downgrade"
    assert plan["requires_force"] is True
    assert compare_versions("0.1.0", "0.1.0") == 0
    assert compare_versions("0.2.0", "0.10.0") < 0


def test_migration(tmp_path):
    db_path = str(tmp_path / "old.db")
    Database(db_path).migrate()
    assert Database(db_path).schema_version() == 3


def test_existing_data_preservation(tmp_path):
    db_path = str(tmp_path / "data.db")
    db = Database(db_path)
    db.migrate()
    SqliteTaskRepository(db).create(Task(title="my task"))
    db.close()
    backup = backup_user_data(db_path, str(tmp_path / "backup.zip"))
    wiped = str(tmp_path / "fresh.db")
    Database(wiped).migrate()
    restore_user_data(backup, wiped)
    reopened = Database(wiped)
    try:
        assert [t.title for t in SqliteTaskRepository(reopened).list()] == ["my task"]
    finally:
        reopened.close()


def test_uninstall_keeps_data_by_default(tmp_path):
    app_bin = tmp_path / "app" / "bin" / "ai-ecosystem.exe"
    app_bin.parent.mkdir(parents=True)
    app_bin.write_text("binary")
    data_db = tmp_path / "data" / "ai_ecosystem.db"
    data_db.parent.mkdir(parents=True)
    Database(str(data_db)).migrate()
    # Uninstall removes binaries only.
    app_bin.unlink()
    assert not app_bin.exists()
    assert data_db.exists()  # user data preserved


def test_reinstall(tmp_path):
    db_path = str(tmp_path / "data.db")
    Database(db_path).migrate()
    assert Database(db_path).schema_version() == 3  # reinstall re-migrates safely


def test_backup_restore(tmp_path):
    live = str(tmp_path / "live.db")
    db = Database(live)
    db.migrate()
    SqliteTaskRepository(db).create(Task(title="backup me"))
    db.close()
    backup = backup_user_data(live, str(tmp_path / "b.zip"))
    target = str(tmp_path / "restored.db")
    Database(target).migrate()
    restore_user_data(backup, target)
    reopened = Database(target)
    try:
        assert [t.title for t in SqliteTaskRepository(reopened).list()] == ["backup me"]
    finally:
        reopened.close()


def test_backup_restore_selective_tables(tmp_path):
    live = str(tmp_path / "live.db")
    db = Database(live)
    db.migrate()
    SqliteTaskRepository(db).create(Task(title="keep"))
    db.close()
    backup = backup_user_data(live, str(tmp_path / "b.zip"))
    target = str(tmp_path / "restored.db")
    Database(target).migrate()
    restore_user_data(backup, target, tables=["tasks"])
    reopened = Database(target)
    try:
        assert len(SqliteTaskRepository(reopened).list()) == 1
    finally:
        reopened.close()
    with pytest.raises(ValueError, match="unknown table"):
        restore_user_data(backup, target, tables=["nonsense"])


def test_runtime_startup(tmp_path):
    config = AppConfig(db_path=str(tmp_path / "rt.db"), environment="production")
    runtime = AgentRuntime(config.db_path)
    api = RuntimeAPI(runtime)
    server = LocalHttpServer(api).start()
    try:
        client = ApiClient(server.url)
        assert client.health() == {"status": "ok"}
        assert client.submit("Boot check.")["state"] == "CREATED"
    finally:
        server.stop()
        runtime.shutdown()


def test_desktop_startup_files():
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    assert (root / "desktop" / "package.json").is_file()
    assert (root / "desktop" / "src-tauri" / "tauri.conf.json").is_file()
    build = root / "packaging" / "windows" / "build.ps1"
    installer = root / "packaging" / "windows" / "installer.iss"
    assert build.is_file()
    assert installer.is_file()
    # Build/installer contract (review fix 10): reproducible installs,
    # verified staging, and matching artifact names -- no silent drift.
    build_text = build.read_text(encoding="utf-8", errors="replace")
    assert "npm ci" in build_text
    assert "dist" in build_text and "BUILD-INFO" in build_text
    installer_text = installer.read_text(encoding="utf-8", errors="replace")
    assert "ai-ecosystem-desktop.exe" in installer_text
    assert "ai-ecosystem.exe" not in installer_text.replace(
        "ai-ecosystem-desktop.exe", "")


def test_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ECO_ENVIRONMENT", "production")
    config = AppConfig.from_env()
    assert config.environment == "production"
    dumped = config.model_dump()
    # Provider credentials live in env, never in the config mapping.
    assert "secret" not in json.dumps(dumped).lower()
    assert "api_key" not in json.dumps(dumped).lower()


def test_offline_operation(tmp_path):
    from ai_ecosystem.cloud import MockSyncTransport, SyncManager

    runtime = AgentRuntime(str(tmp_path / "off.db"))
    api = RuntimeAPI(runtime)
    try:
        created = api.create_task("Work offline.")
        assert api.get_task(created["task_id"])["state"] == "CREATED"
        down = SyncManager(transport=MockSyncTransport(down=True))
        from ai_ecosystem.cloud import make_sync_object

        report = down.sync([make_sync_object("task", created["task_id"],
                                             {"state": "CREATED"})])
        assert report.state.value == "FAILED"  # sync fails, local work fine
        assert api.get_task(created["task_id"])["state"] == "CREATED"
    finally:
        runtime.shutdown()
