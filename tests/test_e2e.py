import io
import json

from fastapi.testclient import TestClient
from PIL import Image
from typer.testing import CliRunner

import hil_eval as hil
from hil_eval.cli import app
from hil_eval.context import get_client
from hil_eval.server import create_app


def test_review_to_export(tmp_path):
    db = tmp_path / "hil.db"
    hil.init("e2e", store=f"sqlite:///{db}", audit_rate=0)
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), "green").save(buffer, format="PNG")

    @hil.review(judge=lambda inputs, output: hil.Judgment(0.2, label="uncertain"), images=["image"])
    def estimate(image, prompt):
        return {"calories": 100}

    assert estimate(buffer.getvalue(), "Estimate calories") == {"calories": 100}
    client = TestClient(create_app(get_client().store))
    items = client.get("/v1/items", params={"project": "e2e"}).json()
    assert items["total"] == 1
    item = items["items"][0]
    assert item["status"] == "pending"
    assert len(item["assets"]) == 1
    response = client.post(
        f"/v1/items/{item['id']}/review",
        json={"action": "edit", "corrected_output": {"calories": 250}, "judge_correct": False},
    )
    assert response.status_code == 200
    output = tmp_path / "export.jsonl"
    runner = CliRunner()
    args = ["--db", str(db), "export", "--project", "e2e", "-o", str(output)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    record = json.loads(output.read_text())
    assert record["corrected_output"] == {"calories": 250}
    assert record["judge_correct"] is False
    result = runner.invoke(app, [*args, "--format", "openai-ft"])
    assert result.exit_code == 0, result.output
    messages = json.loads(output.read_text())["messages"]
    assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert json.loads(messages[1]["content"]) == {"calories": 250}
