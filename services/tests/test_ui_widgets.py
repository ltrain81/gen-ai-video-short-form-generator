import pytest

from shorts import ui_widgets as U


class P:
    sub = "u1"
    tenant_id = "t1"


def test_validate_normalizes_and_flags_interactive(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: {"a1"})
    spec = U.validate({"title": "Pick", "blocks": [
        {"type": "markdown", "text": "hi"},
        {"type": "clips", "id": "c", "clips": [{"assetId": "a1", "startSec": 1, "endSec": 3}]}]}, P())
    assert spec["interactive"] and spec["blocks"][1]["id"] == "c" and spec["blocks"][0]["id"] == "b0"


def test_validate_rejects_foreign_assets(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: {"a1"})
    with pytest.raises(U.WidgetError):
        U.validate({"blocks": [{"type": "media", "assetId": "zzz"}]}, P())


def test_validate_rejects_bad_blocks(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: set())
    for bad in ({"blocks": []}, {"blocks": [{"type": "script"}]}, {"blocks": [{"type": "choice", "id": "x"}]},
                {"blocks": [{"type": "slider", "min": 5, "max": 1}]},
                {"blocks": [{"type": "toggle", "id": "a"}, {"type": "toggle", "id": "a"}]}):
        with pytest.raises(U.WidgetError):
            U.validate(bad, P())


def test_display_only_is_not_interactive(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: set())
    assert not U.validate({"blocks": [{"type": "kv", "items": [{"label": "a", "value": 1}]}]}, P())["interactive"]


def test_missing_asset_id_filled_from_single_asset_project(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: {"a1"})
    spec = U.validate({"blocks": [{"type": "clips", "id": "c", "max": "6", "clips": [{"startSec": "84", "endSec": 111}]},
                                  {"type": "slider", "id": "s", "min": "30", "max": "60", "default": "45"}]}, P(), "a1")
    assert spec["blocks"][0]["clips"][0] == {"assetId": "a1", "startSec": 84.0, "endSec": 111}
    assert spec["blocks"][1]["max"] == 60.0 and spec["blocks"][0]["max"] == 6.0


def test_missing_asset_id_rejected_without_default(monkeypatch):
    monkeypatch.setattr(U.acl, "accessible_asset_ids", lambda p: {"a1"})
    with pytest.raises(U.WidgetError):
        U.validate({"blocks": [{"type": "clips", "id": "c", "clips": [{"startSec": 1, "endSec": 2}]}]}, P())
