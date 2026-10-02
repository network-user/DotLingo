import subprocess

import dotlingo.hardware as hardware


def test_missing_runtime_does_not_spawn_a_probe(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "_LLAMA_PROBE", None)
    monkeypatch.setattr(hardware.importlib.util, "find_spec", lambda _name: None)
    calls: list[object] = []
    monkeypatch.setattr(hardware.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert hardware._llama_gpu_support() == (False, None)
    assert calls == []


def test_probe_timeout_does_not_freeze_the_caller(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "_LLAMA_PROBE", None)
    monkeypatch.setattr(hardware.importlib.util, "find_spec", lambda _name: object())

    def expire(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="llama", timeout=6)

    monkeypatch.setattr(hardware.subprocess, "run", expire)
    assert hardware._llama_gpu_support() == (True, None)
    assert hardware._llama_gpu_support() == (True, None)


def test_probe_reads_the_child_answer(monkeypatch) -> None:
    monkeypatch.setattr(hardware, "_LLAMA_PROBE", None)
    monkeypatch.setattr(hardware.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        hardware.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n"),
    )
    assert hardware._llama_gpu_support() == (True, True)
