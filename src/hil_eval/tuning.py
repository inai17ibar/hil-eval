"""Threshold auto-tuning v0 (see docs/adr/0002-review-and-tuning.md)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .config import DEFAULT_THRESHOLD
from .types import ReviewAction, RoutedBy, ThresholdHistory

_Z_95 = 1.959963984540054
_MIN_SAMPLES = 30
_MAX_STEP = 0.1
_CANDIDATE_STEPS = 101  # t = 0.00, 0.01, ..., 1.00


@dataclass
class TuneResult:
    current: float
    recommended: float
    n_samples: int
    est_review_rate: float | None
    est_miss_rate: float | None
    miss_rate_upper: float | None
    reason: str


@dataclass
class _Sample:
    confidence: float
    judge_correct: bool
    weight: float


def _collect_samples(store, project, threshold, audit_rate):
    """Latest-review-per-item, IPW-weighted (requirements.md §8).

    low_conf-routed items are *always* fully captured (every below-threshold
    call is persisted, deterministically), so they get weight 1. Above-
    threshold calls are only persisted when independently audit-sampled, so
    those get weight 1/audit_rate to reconstruct the full above-threshold
    population. Selection depends on confidence vs. threshold, not on the
    `routed_by` label directly -- an audited call can still land below
    threshold (the audit coin flip happens before the confidence check), and
    such a sample is just as fully captured as a `low_conf` one, so it also
    gets weight 1.
    """
    samples = []
    for item, review in store.list_reviewed(project):
        if item.routed_by == RoutedBy.manual or item.confidence is None:
            continue
        if review.action == ReviewAction.skip:
            continue
        judge_correct = review.judge_correct
        if judge_correct is None:
            judge_correct = review.action == ReviewAction.approve
        if item.confidence < threshold:
            weight = 1.0
        elif item.routed_by == RoutedBy.audit and audit_rate > 0:
            weight = 1.0 / audit_rate
        else:
            # confidence >= threshold but not audit-routed (or audit_rate == 0):
            # this item's selection probability is ill-defined under the current
            # config -- most likely `threshold` moved down since it was
            # collected as `low_conf`. No valid IPW weight; drop it.
            continue
        samples.append(_Sample(item.confidence, judge_correct, weight))
    return samples


def _kish_n_eff(weights):
    """Effective sample size for a weighted set (Kish's approximation)."""
    total = sum(weights)
    if total == 0:
        return 0.0
    return total * total / sum(w * w for w in weights)


def _wilson_upper(phat, n):
    """Upper bound of the 95% Wilson score interval for a proportion."""
    if n <= 0:
        return 0.0
    z2 = _Z_95 * _Z_95
    denom = 1 + z2 / n
    center = phat + z2 / (2 * n)
    margin = _Z_95 * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    return min(1.0, max(0.0, (center + margin) / denom))


def _evaluate(samples, t, total_weight):
    """Review rate R(t) and weighted miss rate M(t) (+ its Wilson upper bound)."""
    below_weight = sum(s.weight for s in samples if s.confidence < t)
    review_rate = below_weight / total_weight if total_weight else 0.0

    above = [s for s in samples if s.confidence >= t]
    above_weight = sum(s.weight for s in above)
    if above_weight == 0:
        # Nothing would be auto-approved at this threshold -> zero miss risk.
        return review_rate, 0.0, 0.0

    miss_weight = sum(s.weight for s in above if not s.judge_correct)
    miss_rate = miss_weight / above_weight
    upper = _wilson_upper(miss_rate, _kish_n_eff([s.weight for s in above]))
    return review_rate, miss_rate, upper


def recommend_threshold(store, project, *, target_miss_rate=None):
    """Compute a recommended threshold without writing anything to the store."""
    config = store.get_project(project)
    if config is None:
        raise ValueError(f"unknown project: {project!r}")
    current = config.threshold if config.threshold is not None else DEFAULT_THRESHOLD
    target = target_miss_rate if target_miss_rate is not None else config.target_miss_rate

    samples = _collect_samples(store, project, current, config.audit_rate)
    n_samples = len(samples)
    if n_samples < _MIN_SAMPLES:
        return TuneResult(
            current=current,
            recommended=current,
            n_samples=n_samples,
            est_review_rate=None,
            est_miss_rate=None,
            miss_rate_upper=None,
            reason="insufficient_data",
        )

    total_weight = sum(s.weight for s in samples)
    for step in range(_CANDIDATE_STEPS):
        t = step / 100
        _, _, upper = _evaluate(samples, t, total_weight)
        if upper <= target:
            recommended = min(current + _MAX_STEP, max(current - _MAX_STEP, t))
            recommended = min(1.0, max(0.0, recommended))
            review_rate, miss_rate, upper = _evaluate(samples, recommended, total_weight)
            reason = "ok" if recommended == t else "clamped"
            return TuneResult(
                current=current,
                recommended=recommended,
                n_samples=n_samples,
                est_review_rate=review_rate,
                est_miss_rate=miss_rate,
                miss_rate_upper=upper,
                reason=reason,
            )

    recommended = min(1.0, current + _MAX_STEP)
    review_rate, miss_rate, upper = _evaluate(samples, recommended, total_weight)
    return TuneResult(
        current=current,
        recommended=recommended,
        n_samples=n_samples,
        est_review_rate=review_rate,
        est_miss_rate=miss_rate,
        miss_rate_upper=upper,
        reason="no_threshold_meets_target",
    )


def apply_tuning(store, project, *, target_miss_rate=None):
    """Recommend a threshold, and if it changes anything, persist it."""
    result = recommend_threshold(store, project, target_miss_rate=target_miss_rate)
    if result.recommended != result.current:
        store.apply_threshold_change(
            ThresholdHistory(
                id=uuid4().hex,
                project=project,
                old=result.current,
                new=result.recommended,
                n_samples=result.n_samples,
                est_review_rate=result.est_review_rate,
                est_miss_rate=result.est_miss_rate,
                created_at=datetime.now(timezone.utc),
            )
        )
    return result
