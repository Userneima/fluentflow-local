"""The local suite must not write into a maintainer's real app-data directory."""

from pathlib import Path

from backend.core import event_logger, job_store, local_config, runtime_paths


def test_module_level_storage_defaults_stay_inside_the_isolated_root(test_runtime_root: Path) -> None:
    frozen = {
        "job_store.DEFAULT_DB_PATH": job_store.DEFAULT_DB_PATH,
        "event_logger.DEFAULT_DB_PATH": event_logger.DEFAULT_DB_PATH,
        "local_config.DEFAULT_CONFIG_PATH": local_config.DEFAULT_CONFIG_PATH,
    }

    escaped = {
        name: str(path)
        for name, path in frozen.items()
        if not Path(path).is_relative_to(test_runtime_root)
    }

    assert not escaped, f"storage defaults resolved outside the test data root: {escaped}"


def test_runtime_path_helpers_default_inside_the_isolated_root(test_runtime_root: Path) -> None:
    assert runtime_paths.app_data_root() == test_runtime_root

    helpers = (
        runtime_paths.default_config_path,
        runtime_paths.default_job_db_path,
        runtime_paths.default_event_db_path,
        runtime_paths.default_source_dir,
        runtime_paths.default_artifact_dir,
        runtime_paths.default_edited_transcript_dir,
        runtime_paths.default_transcript_edit_records_dir,
        runtime_paths.default_video_source_dir,
        runtime_paths.default_codex_export_dir,
    )

    escaped = {
        helper.__name__: str(helper())
        for helper in helpers
        if not helper().is_relative_to(test_runtime_root)
    }

    assert not escaped, f"runtime path helpers resolved outside the test data root: {escaped}"
