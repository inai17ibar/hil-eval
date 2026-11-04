"""Predicate-based confidence judge."""

from ..types import Judgment, validate_unit


def rule(predicate, *, on_true=1.0, on_false=0.0, label=None):
    validate_unit("on_true", on_true)
    validate_unit("on_false", on_false)

    def judge(*args, **kwargs):
        result = predicate(*args, **kwargs)
        if not isinstance(result, bool):
            raise ValueError("predicate must return bool")
        return Judgment(confidence=on_true if result else on_false, label=label)

    return judge
