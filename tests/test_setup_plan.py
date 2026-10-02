from dotlingo.api import Api
from dotlingo.hardware import HardwareSnapshot, plan_setup


def _snapshot(**overrides: object) -> HardwareSnapshot:
    values: dict[str, object] = {
        "cpu_threads": 8,
        "ram_total_gb": 16.0,
        "ram_available_gb": 10.0,
        "disk_free_gb": 40.0,
        "gpu_names": (),
        "gpu_vram_gb": (),
        "llama_runtime_available": True,
        "llama_gpu_offload_available": False,
        "gpu_vram_free_gb": (),
    }
    values.update(overrides)
    return HardwareSnapshot(**values)  # type: ignore[arg-type]


def _model(model_id: str, ram: float, size_gb: float = 1.0) -> dict:
    return {
        "id": model_id,
        "name": model_id,
        "status": "available",
        "estimated_ram_gb": ram,
        "size_bytes": int(size_gb * 1024**3),
    }


def test_installed_model_skips_another_download() -> None:
    plan = plan_setup(_snapshot(), [_model("small", 2), _model("big", 8)], {"big"})
    assert plan["action"] == "ready"
    assert plan["modelId"] == "big"


def test_plan_picks_the_largest_model_that_fits() -> None:
    plan = plan_setup(_snapshot(), [_model("small", 3), _model("big", 8)], set())
    assert plan["action"] == "download"
    assert plan["modelId"] == "big"
    assert "8.0" in str(plan["reason"])


def test_plan_falls_back_to_a_smaller_model() -> None:
    plan = plan_setup(
        _snapshot(ram_available_gb=5.0),
        [_model("big", 12), _model("small", 3)],
        set(),
    )
    assert plan["action"] == "download"
    assert plan["modelId"] == "small"


def test_plan_skips_when_nothing_fits() -> None:
    plan = plan_setup(
        _snapshot(ram_available_gb=2.0, disk_free_gb=1.0),
        [_model("big", 12, 8), _model("small", 8, 4)],
        set(),
    )
    assert plan["action"] == "skip"
    assert plan["modelId"] is None


def test_plan_without_hardware_still_chooses_the_smallest_model() -> None:
    plan = plan_setup(None, [_model("big", 12), _model("small", 3)], set())
    assert plan["action"] == "download"
    assert plan["modelId"] == "small"


def test_plan_survives_a_broken_catalog() -> None:
    plan = plan_setup(
        _snapshot(),
        ["нет", {"id": ""}, {"id": "bare"}, _model("ok", 2)],  # type: ignore[list-item]
        set(),
    )
    assert plan["action"] == "download"
    assert plan["modelId"] == "ok"


def test_plan_skips_an_empty_catalog() -> None:
    plan = plan_setup(_snapshot(), [], set())
    assert plan["action"] == "skip"


def test_runtime_gap_does_not_block_the_download() -> None:
    plan = plan_setup(
        _snapshot(llama_runtime_available=False),
        [_model("small", 3)],
        set(),
    )
    assert plan["action"] == "download"
    assert plan["modelId"] == "small"


def test_api_plan_setup_always_returns_an_action(tmp_path) -> None:
    result = Api(tmp_path).planSetup()
    assert result["ok"] is True
    assert result["data"]["action"] in {"download", "ready", "skip"}
    if result["data"]["action"] != "skip":
        assert result["data"]["modelId"]
