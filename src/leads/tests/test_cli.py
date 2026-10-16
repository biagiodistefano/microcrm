"""Smoke tests for the CLI commands added for outreach messages and the new lead fields."""

import importlib.util
import json
import typing as t
from pathlib import Path

import pytest
from typer.testing import CliRunner

_spec = importlib.util.spec_from_file_location("microcrm_cli", Path(__file__).resolve().parents[3] / "cli.py")
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

runner = CliRunner()


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, t.Any]] = []

    def _record(self, method: str, path: str, data: t.Any = None) -> dict[str, t.Any]:
        self.calls.append((method, path, data))
        return {
            "id": 1,
            "name": "n",
            "channel": "instagram",
            "deeplink": "https://ig.me/m/x",
            "created": True,
            "subject": "S",
        }

    def get(self, path: str, params: t.Any = None) -> dict[str, t.Any]:
        self.calls.append(("get", path, params))
        return {"count": 0, "results": []}

    def post(self, path: str, data: t.Any = None) -> dict[str, t.Any]:
        return self._record("post", path, data)

    def patch(self, path: str, data: t.Any) -> dict[str, t.Any]:
        return self._record("patch", path, data)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    f = FakeApi()
    monkeypatch.setattr(cli, "api", f)
    return f


def test_messages_commands(fake: FakeApi) -> None:
    assert runner.invoke(cli.app, ["messages", "list", "--status", "draft", "--batch", "b1"]).exit_code == 0
    assert fake.calls[-1][1] == "/messages/" and fake.calls[-1][2]["status"] == "draft"
    r = runner.invoke(
        cli.app,
        ["messages", "create", "-l", "3", "-c", "instagram", "-t", "hey", "--assigned-to", "w@x.io", "--batch", "b"],
    )
    assert r.exit_code == 0
    method, path, data = fake.calls[-1]
    assert (method, path) == ("post", "/messages/")
    assert data["lead_id"] == 3 and data["assigned_to_email"] == "w@x.io" and data["text"] == "hey"
    assert runner.invoke(cli.app, ["messages", "get", "1"]).exit_code == 0
    assert runner.invoke(cli.app, ["messages", "mark-sent", "1"]).exit_code == 0
    assert fake.calls[-1][1] == "/messages/1/mark-sent"
    assert runner.invoke(cli.app, ["messages", "skip", "1"]).exit_code == 0
    assert fake.calls[-1][1] == "/messages/1/skip"


def test_leads_create_new_fields(fake: FakeApi) -> None:
    r = runner.invoke(
        cli.app,
        ["leads", "create", "--name", "X", "--bluesky", "x.bsky", "--whatsapp", "+49", "--external-ref", "r1",
         "--ticketing-platform", "ra", "--ticketing-platform", "eventbrite"],
    )  # fmt: skip
    assert r.exit_code == 0
    data = fake.calls[-1][2]
    assert data["bluesky"] == "x.bsky" and data["external_ref"] == "r1"
    assert data["ticketing_platforms"] == ["ra", "eventbrite"]


def test_import_upsert(fake: FakeApi, tmp_path: Path) -> None:
    f = tmp_path / "leads.json"
    f.write_text(json.dumps([{"name": "A", "linkedin": None, "external_ref": "r1"}]))
    assert runner.invoke(cli.app, ["leads", "import", str(f), "--upsert"]).exit_code == 0
    assert fake.calls[-1][1] == "/leads/upsert" and fake.calls[-1][2]["linkedin"] == ""


def test_drafts_create_assignment(fake: FakeApi) -> None:
    r = runner.invoke(
        cli.app,
        ["drafts", "create", "-l", "1", "-s", "S", "-b", "B", "--assigned-to", "w@x.io", "--from-email", "w@x.io"],
    )
    assert r.exit_code == 0
    assert fake.calls[-1][2]["assigned_to_email"] == "w@x.io" and fake.calls[-1][2]["from_email"] == "w@x.io"
