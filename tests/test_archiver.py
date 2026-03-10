"""Tests for state file archiver."""
import json
import time
from datetime import datetime, timezone

import pytest
from modules.archiver import StateArchiver


class TestArchiveGuardState:
    def test_moves_file_to_date_directory(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()
        state_file = guard_dir / "apex-slot-0.json"
        state_file.write_text(json.dumps({"closed": True}))

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        result = archiver.archive_guard_state(str(guard_dir), "apex-slot-0")

        assert result is True
        assert not state_file.exists()

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archived = tmp_path / "archive" / date_str / "guard" / "apex-slot-0.json"
        assert archived.exists()
        data = json.loads(archived.read_text())
        assert data["closed"] is True

    def test_returns_false_for_nonexistent_file(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        result = archiver.archive_guard_state(str(guard_dir), "no-such-file")

        assert result is False


class TestArchiveSlotSnapshot:
    def test_writes_json_with_correct_data(self, tmp_path):
        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        slot_data = {
            "slot_id": 2,
            "instrument": "ETH-PERP",
            "direction": "long",
            "pnl": 42.5,
        }
        archiver.archive_slot_snapshot(slot_data, slot_id=2)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        apex_dir = tmp_path / "archive" / date_str / "apex"
        assert apex_dir.exists()

        files = list(apex_dir.glob("slot-2-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["slot_id"] == 2
        assert data["instrument"] == "ETH-PERP"
        assert data["pnl"] == 42.5


class TestArchiveOld:
    def test_filters_closed_files_only(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()

        # Closed file
        (guard_dir / "closed-pos.json").write_text(
            json.dumps({"state": {"closed": True}})
        )
        # Active file
        (guard_dir / "active-pos.json").write_text(
            json.dumps({"state": {"closed": False}})
        )

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(guard_dir))

        assert counts["guard"] == 1
        assert counts["skipped"] == 1
        assert not (guard_dir / "closed-pos.json").exists()
        assert (guard_dir / "active-pos.json").exists()

    def test_respects_days_old_parameter(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()

        old_file = guard_dir / "old-pos.json"
        old_file.write_text(json.dumps({"state": {"closed": True}}))
        # Make file appear old (3 days ago)
        import os
        old_time = time.time() - (3 * 86400)
        os.utime(str(old_file), (old_time, old_time))

        new_file = guard_dir / "new-pos.json"
        new_file.write_text(json.dumps({"state": {"closed": True}}))
        # new_file keeps current mtime

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(guard_dir), days_old=2)

        assert counts["guard"] == 1  # only old file
        assert counts["skipped"] == 1  # new file skipped (too recent)
        assert not old_file.exists()
        assert new_file.exists()

    def test_dry_run_does_not_move_files(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()

        closed_file = guard_dir / "closed-pos.json"
        closed_file.write_text(json.dumps({"state": {"closed": True}}))

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(guard_dir), dry_run=True)

        assert counts["guard"] == 1
        assert closed_file.exists()  # NOT moved
        assert not (tmp_path / "archive").exists()

    def test_trades_jsonl_never_touched(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()

        # Place a trades.jsonl in the guard dir (shouldn't be archived)
        trades_file = guard_dir / "trades.jsonl"
        trades_file.write_text('{"trade": 1}\n')

        # Place a closed state file
        (guard_dir / "slot-0.json").write_text(
            json.dumps({"state": {"closed": True}})
        )

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(guard_dir))

        # trades.jsonl should still exist in guard_dir (it's not .json glob match)
        assert trades_file.exists()

        # Check archive directory has no trades.jsonl
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_guard = tmp_path / "archive" / date_str / "guard"
        if archive_guard.exists():
            archived_files = [f.name for f in archive_guard.iterdir()]
            assert "trades.jsonl" not in archived_files

    def test_nonexistent_guard_dir(self, tmp_path):
        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(tmp_path / "no-such-dir"))
        assert counts == {"guard": 0, "skipped": 0}

    def test_malformed_json_skipped(self, tmp_path):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()
        (guard_dir / "bad.json").write_text("not valid json {{{")

        archiver = StateArchiver(archive_dir=str(tmp_path / "archive"))
        counts = archiver.archive_old(str(guard_dir))

        assert counts["skipped"] == 1
        assert counts["guard"] == 0
