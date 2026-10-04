from pathlib import Path

from dotlingo.api import Api
from dotlingo.dialogs import delete_dialog, list_dialogs, load_dialog, read_attachment, save_dialog


def test_dialogs_roundtrip_and_reject_a_bad_id(tmp_path: Path) -> None:
    saved = save_dialog(
        tmp_path,
        {
            "id": "a" * 32,
            "title": "Первый",
            "mode": "ask",
            "messages": [{"id": "1", "role": "user", "text": "Привет"}],
        },
    )
    assert saved["title"] == "Первый"
    assert saved["projectId"] == ""
    bound = save_dialog(
        tmp_path,
        {"id": "b" * 32, "title": "В проекте", "projectId": "proj/../x", "messages": []},
    )
    assert bound["projectId"] == ""
    kept = save_dialog(
        tmp_path,
        {"id": "c" * 32, "title": "Связан", "projectId": "abc-123", "messages": []},
    )
    assert kept["projectId"] == "abc-123"
    listed = {item["id"]: item for item in list_dialogs(tmp_path)}
    assert listed["c" * 32]["projectId"] == "abc-123"
    assert load_dialog(tmp_path, "a" * 32)["messages"][0]["text"] == "Привет"
    delete_dialog(tmp_path, "a" * 32)
    delete_dialog(tmp_path, "b" * 32)
    delete_dialog(tmp_path, "c" * 32)
    assert list_dialogs(tmp_path) == []
    try:
        save_dialog(tmp_path, {"id": "../escape", "messages": []})
    except ValueError:
        return
    raise AssertionError("чужой путь не должен сохраняться")


def test_attachment_reads_a_text_file_and_clips_it(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("строка\n" * 20, encoding="utf-8")
    loaded = read_attachment(path)
    assert loaded["name"] == "note.txt"
    assert "строка" in loaded["text"]
    assert loaded["chars"] > 0


def test_dialog_meter_reports_context_percent(tmp_path: Path) -> None:
    api = Api(tmp_path)
    result = api.dialogMeter({"modelId": "missing", "charCount": 4000})
    assert result["ok"] is True
    data = result["data"]
    assert data["contextLimit"] == 2048
    assert data["contextUsed"] == 1000
    assert data["contextPercent"] == min(100, round(1000 * 100 / 2048))
    assert data["placement"] == "CPU"
