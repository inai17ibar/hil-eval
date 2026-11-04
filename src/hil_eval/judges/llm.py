"""Optional LLM judge; HTTP transport has no third-party dependencies."""

import json
import os
import urllib.request

from ..types import Judgment


def llm(model, rubric, *, provider="openai", api_key=None, api_base=None, provider_fn=None):
    if provider not in {"openai", "anthropic"}:
        raise ValueError("provider must be openai or anthropic")

    def judge(*args, **kwargs):
        prompt = (
            f"Evaluate using this rubric: {rubric}\n"
            "Return only strict JSON with confidence (number in [0,1]), label and reason.\n"
            + json.dumps({"args": args, "kwargs": kwargs}, default=str, ensure_ascii=False)
        )
        if provider_fn is not None:
            raw = provider_fn(prompt)
        else:
            key = api_key or os.environ.get(
                "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
            )
            if not key:
                raise ValueError("API key is required")
            headers = {"Content-Type": "application/json"}
            body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            if provider == "openai":
                url = (api_base or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
                headers["Authorization"] = f"Bearer {key}"
            else:
                url = (api_base or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
                headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
                body["max_tokens"] = 1024
            request = urllib.request.Request(
                url, data=json.dumps(body).encode(), headers=headers, method="POST"
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
            raw = (
                data["choices"][0]["message"]["content"]
                if provider == "openai"
                else data["content"][0]["text"]
            )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM response must be a JSON object")
        for name in ("label", "reason"):
            if data.get(name) is not None and not isinstance(data[name], str):
                raise ValueError(f"LLM {name} must be a string or null")
        return Judgment(
            confidence=data["confidence"], label=data.get("label"), reason=data.get("reason")
        )

    return judge
