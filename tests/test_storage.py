"""Interrupted writes must recover without losing or duplicating events."""

import json
import shutil
import subprocess
import sys

import pytest

from aidm import saveload, store
from aidm.domain import Entity
from aidm.mcp import tools
from aidm.store import transaction
from aidm.store.io import atomic_write_text
from tests.test_regressions import GAMES


def test_failed_event_log_write_recovers_once(world, monkeypatch):
    with monkeypatch.context() as patch:

        def fail(*_args):
            message = "injected disk failure"
            raise OSError(message)

        patch.setattr(transaction, "append_records", fail)
        with pytest.raises(OSError, match="injected"):
            tools.propose_state_change(world, "player", {"money": 11, "status": "resting"})
    assert (world / transaction.JOURNAL).exists()
    store.recover_pending(world)
    store.recover_pending(world)
    assert not (world / transaction.JOURNAL).exists()
    assert store.load_entity(world, "player").model_dump()["money"] == 11
    assert store.load_entity(world, "player").model_dump()["status"] == "resting"
    assert len(store.read_events(world)) == 2
    assert len({e.id for e in store.read_events(world)}) == 2


def test_journal_left_after_commit_does_not_duplicate_events(world):
    event = store.new_event(
        world, "state_changed", targets=["player"], payload={"field": "money", "value": 3}
    )
    store.append_event(world, event)
    pending = transaction.PendingEvents(
        events=[event], entities=[store.load_entity(world, "player")]
    )
    (world / transaction.JOURNAL).write_text(pending.model_dump_json())
    store.recover_pending(world)
    assert store.read_events(world) == [event]


def test_abrupt_process_exit_recovers_on_reload(world):
    code = """
import os
import sys
from pathlib import Path
from aidm.mcp import tools
from aidm.store import transaction
transaction.append_records = lambda *_args: os._exit(73)
tools.propose_state_change(Path(sys.argv[1]), 'player', {'money': 4})
"""
    result = subprocess.run([sys.executable, "-c", code, str(world)], timeout=15, check=False)  # noqa: S603
    assert result.returncode == 73
    assert (world / transaction.JOURNAL).exists()
    saveload.validate_save(world)
    assert store.load_entity(world, "player").model_dump()["money"] == 4
    assert len(store.read_events(world)) == 1


def test_invalid_second_projection_writes_nothing(world):
    before = store.load_entity(world, "player")
    event = store.new_event(
        world, "state_changed", targets=["player"], payload={"field": "money", "value": 3}
    )
    invalid = event.model_copy(
        update={"id": "evt_0002", "payload": {"field": "traits", "value": 4}}
    )
    with pytest.raises(ValueError):
        store.append_events(world, [event, invalid])
    assert store.load_entity(world, "player") == before
    assert store.read_events(world) == []
    assert not (world / transaction.JOURNAL).exists()


def test_corrupt_log_is_preserved_and_rejected(world):
    path = world / "events" / "log.jsonl"
    path.parent.mkdir()
    path.write_text('{"torn":')
    with pytest.raises(ValueError, match="line 1"):
        tools.propose_state_change(world, "player", {"money": 3})
    assert path.read_text() == '{"torn":'


@pytest.mark.parametrize("record", ["null", "[]", "{}", '{"turn":0,"input":3,"narration":"x"}'])
def test_malformed_transcript_is_rejected(world, record):
    path = world / "transcript.jsonl"
    path.write_text(record)
    with pytest.raises(ValueError, match="line 1"):
        store.read_transcript(world)
    assert path.read_text() == record


def test_symlink_save_cannot_be_loaded_or_deleted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "saves").mkdir()
    external = tmp_path / "external"
    shutil.copytree(GAMES / "example", external)
    (tmp_path / "saves" / "linked").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        saveload.delete_save("linked")
    with pytest.raises(ValueError):
        saveload.save_path("linked")
    assert (external / "scenario.json").exists()


def test_symlink_entity_cannot_be_read_or_written(world, tmp_path):
    external = tmp_path / "external.json"
    external.write_text('{"slug":"other","type":"character","name":"Other"}')
    (world / "characters" / "other.json").symlink_to(external)
    with pytest.raises(ValueError):
        store.load_entity(world, "other")
    with pytest.raises(ValueError):
        store.save_entity(world, Entity(slug="other", type="character", name="Changed"))
    assert json.loads(external.read_text())["name"] == "Other"


def test_atomic_write_failure_preserves_original(tmp_path, monkeypatch):
    from pathlib import Path  # noqa: PLC0415

    target = tmp_path / "state.json"
    target.write_text("before")

    def fail(*_args):
        message = "injected replace failure"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_text(target, "after")
    assert target.read_text() == "before"
    assert list(tmp_path.iterdir()) == [target]
