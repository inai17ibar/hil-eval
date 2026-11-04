"""Convert an extracted score to confidence."""

import math

from ..types import Judgment


def logprob(extractor, *, transform="sigmoid"):
    """Extract a score by callable or key (output first, then captured inputs)."""
    if transform not in {"sigmoid", "identity"}:
        raise ValueError("transform must be sigmoid or identity")

    def judge(*args, **kwargs):
        if callable(extractor):
            value = extractor(*args, **kwargs)
        elif len(args) > 1 and extractor == "output":
            value = args[1]
        elif len(args) > 1 and isinstance(args[1], dict) and extractor in args[1]:
            value = args[1][extractor]
        elif args:
            value = args[0][extractor]
        elif isinstance(kwargs.get("output"), dict) and extractor in kwargs["output"]:
            value = kwargs["output"][extractor]
        else:
            value = kwargs[extractor]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("logprob score must be finite numeric")
        if transform == "sigmoid":
            value = (
                1 / (1 + math.exp(-value))
                if value >= 0
                else math.exp(value) / (1 + math.exp(value))
            )
        return Judgment(confidence=value)

    return judge
