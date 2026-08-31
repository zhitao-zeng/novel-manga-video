import os
from pathlib import Path

import pytest

os.environ.setdefault(
    "NOVEL_MODEL_MANIFEST",
    str(Path(__file__).parents[1] / "runtime/model_manifest.json"),
)
os.environ.setdefault("NOVEL_RUNTIME_LOG_ROOT", "/tmp/novel-manga-test-runtime")

import runtime.model_supervisor as supervisor_runtime
from runtime.model_supervisor import ModelSupervisor, _h3_cache_arguments


def _video_command() -> list[str]:
    supervisor = object.__new__(ModelSupervisor)
    supervisor.models = {"video": Path("/models/minimax-h3")}
    return supervisor._command("video")


def test_h3_supervisor_auto_falls_back_when_gpu_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOVEL_MINIMAX_H3_CACHE_LRU", raising=False)
    monkeypatch.setattr(supervisor_runtime, "_visible_gpu_memory_mib", lambda: None)

    command = _video_command()

    assert "--cache-none" in command
    assert "--cache-lru" not in command


def test_h3_supervisor_auto_enables_lru_8_on_exclusive_a100_80gb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "auto")
    monkeypatch.setattr(
        supervisor_runtime,
        "_visible_gpu_memory_mib",
        lambda: [(81920, 81000)],
    )

    command = _video_command()

    index = command.index("--cache-lru")
    assert command[index + 1] == "8"
    assert "--cache-none" not in command


@pytest.mark.parametrize(
    "memory",
    (
        [(81920, 69999)],
        [(79999, 79000)],
        [(81920, 81000), (81920, 81000)],
    ),
)
def test_h3_supervisor_auto_uses_cache_none_when_resource_contract_is_not_met(
    monkeypatch: pytest.MonkeyPatch,
    memory: list[tuple[int, int]],
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "auto")
    monkeypatch.setattr(
        supervisor_runtime,
        "_visible_gpu_memory_mib",
        lambda: memory,
    )

    command = _video_command()

    assert "--cache-none" in command
    assert "--cache-lru" not in command


def test_h3_supervisor_can_explicitly_disable_lru(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "0")
    monkeypatch.setattr(
        supervisor_runtime,
        "_visible_gpu_memory_mib",
        lambda: [(81920, 81000)],
    )

    command = _video_command()

    assert "--cache-none" in command
    assert "--cache-lru" not in command


def test_h3_supervisor_can_opt_into_validated_lru_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "8")

    command = _video_command()

    index = command.index("--cache-lru")
    assert command[index + 1] == "8"
    assert "--cache-none" not in command


def test_h3_supervisor_rejects_negative_lru_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "-1")

    with pytest.raises(ValueError, match="must be non-negative"):
        _h3_cache_arguments()


def test_h3_supervisor_rejects_invalid_lru_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_MINIMAX_H3_CACHE_LRU", "fast")

    with pytest.raises(ValueError, match="must be 'auto'"):
        _h3_cache_arguments()
