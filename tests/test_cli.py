import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import uvicorn
from typer.testing import CliRunner

import hil_eval as hil
from hil_eval.cli import app
from hil_eval.context import get_client
from hil_eval.types import Review, ReviewAction, ThresholdHistory

runner = CliRunner()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    hil.init("cli", store=f"sqlite:///{path}")
    item = hil.submit("hello", "world")
    get_client().store.save_review(
        Review(
            id=uuid4().hex,
            item_id=item.id,
            reviewer=None,
            action=ReviewAction.approve,
            created_at=datetime.now(timezone.utc),
        )
    )
    return str(path)


def test_export(db, tmp_path):
    output = tmp_path / "out.jsonl"
    result = runner.invoke(app, ["--db", db, "export", "--project", "cli", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["corrected_output"] == "world"
    result = runner.invoke(
        app,
        [
            "--db",
            db,
            "export",
            "--project",
            "cli",
            "--status",
            "approved",
            "--status",
            "corrected",
            "--format",
            "openai-ft",
            "--no-images",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["messages"][1]["content"] == "world"
    assert (
        runner.invoke(app, ["--db", db, "export", "--project", "cli", "--format", "bad"]).exit_code
        != 0
    )


def test_tune_projects_history(db):
    for command in [
        ["tune", "--project", "cli", "--apply"],
        ["projects"],
        ["history", "--project", "cli"],
    ]:
        result = runner.invoke(app, ["--db", db, *command])
        assert result.exit_code == 0, result.output
    assert (
        "insufficient_data" in runner.invoke(app, ["--db", db, "tune", "--project", "cli"]).output
    )
    assert "cli" in runner.invoke(app, ["projects"], env={"HIL_DB": db}).output
    store = get_client().store
    store.add_threshold_history(
        ThresholdHistory(
            id=uuid4().hex,
            project="cli",
            old=0.8,
            new=0.9,
            n_samples=30,
            est_review_rate=0.5,
            est_miss_rate=0.01,
            created_at=datetime.now(timezone.utc),
        )
    )
    assert "0.9" in runner.invoke(app, ["--db", db, "history", "--project", "cli"]).output
    assert runner.invoke(app, ["--db", db, "tune", "--project", "absent"]).exit_code != 0


def test_serve_and_ui(db, monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    result = runner.invoke(app, ["serve", "--db", db, "--host", "0.0.0.0", "--no-ui"])
    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert calls[-1][1] == {"host": "0.0.0.0", "port": 8765}
    assert not any(getattr(r, "path", "") == "/{full_path:path}" for r in calls[-1][0].routes)
    result = runner.invoke(
        app, ["--db", db, "serve", "--port", "9000"], env={"HIL_API_KEY": "secret"}
    )
    assert result.exit_code == 0, result.output
    from fastapi.testclient import TestClient

    assert TestClient(calls[-1][0]).get("/v1/projects").status_code == 401
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert runner.invoke(app, ["--db", db, "ui"]).exit_code == 0
    assert opened == ["http://127.0.0.1:8765"]
    assert runner.invoke(app, ["--db", db, "ui", "--no-browser"]).exit_code == 0
    assert len(opened) == 1


def test_missing_typer_message(monkeypatch):
    import builtins
    import runpy

    from hil_eval import cli

    original_import = builtins.__import__

    def without_typer(name, *args, **kwargs):
        if name == "typer":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_typer)
    namespace = runpy.run_path(cli.__file__, run_name="hil_eval._cli_without_typer")
    with pytest.raises(SystemExit, match=r"pip install hil-eval\[cli\]"):
        namespace["main"]()


def test_db_option_accepted_after_subcommand(tmp_path):
    from typer.testing import CliRunner

    from hil_eval.cli import app

    db = str(tmp_path / "hil.db")
    result = CliRunner().invoke(app, ["projects", "--db", db])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "hil.db").exists()
