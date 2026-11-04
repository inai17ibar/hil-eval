import json
import urllib.request

import pytest

import hil_eval as hil
from hil_eval.judges import llm, logprob, rule
from hil_eval.store import SQLiteStore


@pytest.mark.parametrize("value,expected", [(0, 0.5), (1000, 1), (-1000, 0)])
def test_logprob_sigmoid(value, expected):
    assert logprob(lambda inputs, output: output)({}, value).confidence == expected
    assert logprob("output")({}, value).confidence == expected


@pytest.mark.parametrize("value", [True, "1", None, float("nan"), float("inf")])
def test_logprob_invalid(value):
    with pytest.raises((ValueError, TypeError)):
        logprob(lambda: value)()


def test_identity_and_rule_boundaries():
    assert logprob("x", transform="identity")({"x": 1}, 0).confidence == 1
    assert rule(lambda i, o: o == "ok", label="check")({}, "ok").confidence == 1
    assert rule(lambda i, o: False)({}, "ok").confidence == 0
    with pytest.raises(ValueError):
        logprob(lambda: 2, transform="identity")()
    with pytest.raises(ValueError):
        rule(lambda: 1)()
    with pytest.raises(ValueError):
        rule(lambda: True, on_true=float("nan"))


def test_llm_injected_no_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("transport used")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    prompts = []

    def provider(prompt):
        prompts.append(prompt)
        return json.dumps(dict(confidence=0.75, label="ok", reason="matches"))

    result = llm("model", "rubric", provider_fn=provider)({"x": 1}, "answer")
    assert result.confidence == 0.75 and result.reason == "matches"
    assert "rubric" in prompts[0] and "answer" in prompts[0]


@pytest.mark.parametrize(
    "raw", ["oops", "{}", '{"confidence": true}', '{"confidence": 2}', '{"confidence": NaN}']
)
def test_llm_invalid_response(raw):
    with pytest.raises((ValueError, KeyError, TypeError)):
        llm("model", "rubric", provider_fn=lambda _: raw)({}, 1)


@pytest.mark.parametrize(
    "judge",
    [
        llm("m", "r", provider_fn=lambda _: "bad"),
        logprob(lambda i, o: float("nan")),
        rule(lambda i, o: 1),
    ],
)
def test_invalid_judges_fail_open_in_decorator(tmp_path, judge):
    hil.init(project="p", store=f"sqlite:///{tmp_path}/db.sqlite")
    output = object()

    @hil.review(judge=judge, audit_rate=1)
    def function():
        return output

    assert function() is output
    saved = SQLiteStore(tmp_path / "db.sqlite").list_items("p")[0]
    assert saved.confidence is None and "judge_error" in saved.judge_reason


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_provider_request_shapes(monkeypatch, provider):
    from io import BytesIO

    captured = []
    answer = json.dumps(dict(confidence=0.4, label=None, reason="fine"))

    def transport(request, **kwargs):
        captured.append(request)
        payload = (
            {"choices": [{"message": {"content": answer}}]}
            if provider == "openai"
            else {"content": [{"text": answer}]}
        )
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    assert (
        llm("m", "r", provider=provider, api_key="secret", api_base="http://test/v1")(
            {}, 1
        ).confidence
        == 0.4
    )
    body = json.loads(captured[0].data)
    assert body["model"] == "m"
    assert captured[0].full_url.endswith(
        "/chat/completions" if provider == "openai" else "/messages"
    )


def test_logprob_string_extracts_model_output():
    judge = logprob("score")
    assert judge({"score": 100}, {"score": 0}).confidence == 0.5
    assert judge(output={"score": 0}).confidence == 0.5
    with pytest.raises(KeyError):
        judge({}, {})


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"confidence": .5}',
        '{"confidence": 0.5, "label": {}}',
        '{"confidence": 0.5, "reason": []}',
    ],
)
def test_llm_rejects_wrong_response_shape(raw):
    with pytest.raises(ValueError):
        llm("m", "r", provider_fn=lambda _: raw)({}, 1)
