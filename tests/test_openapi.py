from pathlib import Path

from typer.testing import CliRunner

from hil_eval.cli import app


def test_openapi_snapshot(tmp_path):
    output = tmp_path / "openapi.yaml"
    result = CliRunner().invoke(app, ["openapi", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_text() == Path("openapi.yaml").read_text()


def test_response_contracts(tmp_path):
    from hil_eval.server import create_app
    from hil_eval.store import SQLiteStore

    spec = create_app(SQLiteStore(tmp_path / "db")).openapi()
    schemas = spec["components"]["schemas"]
    for name, fields in {
        "ItemResponse": ["id", "assets", "options", "expires_at", "status"],
        "ItemDetailResponse": ["reviews"],
        "AssetResponse": ["role", "bbox", "url"],
        "ReviewResponse": ["choice", "reason_codes", "corrected_output"],
        "QueueResponse": ["items", "total"],
        "ProjectResponse": ["name", "threshold"],
    }.items():
        assert set(fields) <= set(schemas[name]["required"])
    for path, methods in spec["paths"].items():
        if path.startswith("/v1/") and "blobs" not in path:
            for operation in methods.values():
                for code, response in operation["responses"].items():
                    if code.startswith("2") and code != "204":
                        assert response["content"]["application/json"]["schema"]


def test_typescript_response_fields_match_schema(tmp_path):
    import re

    from hil_eval.server import create_app
    from hil_eval.store import SQLiteStore

    schemas = create_app(SQLiteStore(tmp_path / "db")).openapi()["components"]["schemas"]
    for filename in ["ui/src/api.ts", "clients/ts/src/index.ts"]:
        source = Path(filename).read_text()
        for ts, model in [
            ("Asset", "AssetResponse"),
            ("Review", "ReviewResponse"),
            ("Item", "ItemResponse"),
            ("Project", "ProjectResponse"),
            ("Queue", "QueueResponse"),
            ("ReasonCode", "ReasonCodeResponse"),
        ]:
            body = re.search(r"export interface " + ts + r" \{(.*?)\n?\}", source, re.S)[1]
            fields = set(re.findall(r"(?:^|;)\s*(\w+)\??:", body))
            assert fields - {"reviews"} == set(schemas[model]["properties"]), (filename, ts)
        assert 'role: "input" | "output" | "context"' in source
        assert "judge_metadata: Record<string, Json>" in source
        assert "reviews: Review[]" in source
