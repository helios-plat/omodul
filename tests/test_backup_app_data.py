import sys
from unittest.mock import MagicMock, patch
sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

import pytest
from pathlib import Path
from omodul.backup_app_data import backup_app_data, BackupAppDataConfig, BackupAppDataInput

def test_backup_fingerprint_stability():
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    from omodul.backup_app_data import compute_fingerprint_for_backup_app_data
    fp1 = compute_fingerprint_for_backup_app_data(config, input_data)
    fp2 = compute_fingerprint_for_backup_app_data(config, input_data)
    assert fp1 == fp2

def test_backup_completed_normal_path(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    
    with patch("omodul.backup_app_data.dir_archive_to_targz") as mock_archive:
        with patch("omodul.backup_app_data.s3_upload_file") as mock_upload:
            with patch("omodul.backup_app_data.s3_object_metadata") as mock_meta:
                result = backup_app_data(config, input_data, tmp_path)
                assert result["status"] == "completed"
                assert "s3://b/" in result["findings"].storage_url
                assert result["findings"].file_count == 1

def test_backup_archive_failure(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    
    with patch("omodul.backup_app_data.dir_archive_to_targz") as mock_archive:
        mock_archive.side_effect = Exception("Disk full")
        result = backup_app_data(config, input_data, tmp_path)
        assert result["status"] == "failed"
        assert "Disk full" in result["error"]["error_message"]

def test_backup_decision_trail(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    
    with patch("omodul.backup_app_data.dir_archive_to_targz"):
        with patch("omodul.backup_app_data.s3_upload_file"):
            with patch("omodul.backup_app_data.s3_object_metadata"):
                result = backup_app_data(config, input_data, tmp_path)
                assert len(result["decision_trail"]["steps"]) >= 3

def test_backup_fingerprint_changes_whitelist():
    config1 = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b", scope="full", time_window="w1")
    config2 = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b", scope="full", time_window="w2")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    from omodul.backup_app_data import compute_fingerprint_for_backup_app_data
    assert compute_fingerprint_for_backup_app_data(config1, input_data) != compute_fingerprint_for_backup_app_data(config2, input_data)

def test_backup_invariant_outside_whitelist():
    config1 = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b", scope="full", time_window="w1", compression="gzip")
    config2 = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b", scope="full", time_window="w1", compression="none")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    from omodul.backup_app_data import compute_fingerprint_for_backup_app_data
    assert compute_fingerprint_for_backup_app_data(config1, input_data) == compute_fingerprint_for_backup_app_data(config2, input_data)

def test_backup_report_exists(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    with patch("omodul.backup_app_data.dir_archive_to_targz"):
        with patch("omodul.backup_app_data.s3_upload_file"):
            with patch("omodul.backup_app_data.s3_object_metadata"):
                result = backup_app_data(config, input_data, tmp_path)
                assert result["report_path"].exists()

def test_backup_on_step_forwarding(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    steps = []
    def on_step(s): steps.append(s)
    with patch("omodul.backup_app_data.dir_archive_to_targz"):
        with patch("omodul.backup_app_data.s3_upload_file"):
            with patch("omodul.backup_app_data.s3_object_metadata"):
                backup_app_data(config, input_data, tmp_path, on_step=on_step)
                assert len(steps) >= 3

def test_backup_no_user_id_in_signature():
    import inspect
    sig = inspect.signature(backup_app_data)
    assert "user_id" not in sig.parameters

def test_backup_id_generation(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=["/v"], config_paths=[])
    with patch("omodul.backup_app_data.dir_archive_to_targz"):
        with patch("omodul.backup_app_data.s3_upload_file"):
            with patch("omodul.backup_app_data.s3_object_metadata"):
                result = backup_app_data(config, input_data, tmp_path)
                assert result["findings"].backup_id.startswith(result["fingerprint"][:16])

def test_backup_with_config_paths(tmp_path):
    config = BackupAppDataConfig(instance_name="g-prod", backup_target="s3://b/", scope="full", time_window="manual")
    input_data = BackupAppDataInput(container_id="c1", volumes_to_backup=[], config_paths=["/etc/app.conf"])
    with patch("omodul.backup_app_data.dir_archive_to_targz"):
        with patch("omodul.backup_app_data.s3_upload_file"):
            with patch("omodul.backup_app_data.s3_object_metadata"):
                result = backup_app_data(config, input_data, tmp_path)
                assert result["findings"].file_count == 1
