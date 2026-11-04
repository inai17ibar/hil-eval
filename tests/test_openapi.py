from pathlib import Path

from typer.testing import CliRunner

from hil_eval.cli import app


def test_openapi_snapshot(tmp_path):
    output = tmp_path / "openapi.yaml"
    result = CliRunner().invoke(app, ["openapi", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_text() == Path("openapi.yaml").read_text()
