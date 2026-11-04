from datetime import datetime, timezone

import pytest

from hil_eval.store import SQLiteStore
from hil_eval.tuning import _collect_samples, _evaluate, apply_tuning, recommend_threshold
from hil_eval.types import Item, Review, ReviewAction, RoutedBy, Status

_counter = 0


def add_item(store, *, project="p", confidence, routed_by, action, judge_correct=None):
    """Create and immediately review an item, mirroring the real routing/review flow."""
    global _counter
    _counter += 1
    item_id = f"item-{_counter}"
    store.save_item(
        Item(
            id=item_id,
            project=project,
            fn_name="fn",
            input={},
            output={},
            confidence=confidence,
            judge_label=None,
            judge_reason=None,
            judge_metadata={},
            routed_by=routed_by,
            status=Status.pending,
            tags={},
            trace_id=None,
            created_at=datetime.now(timezone.utc),
            assets=[],
        )
    )
    store.save_review(
        Review(
            id=f"review-{_counter}",
            item_id=item_id,
            reviewer=None,
            action=action,
            judge_correct=judge_correct,
            created_at=datetime.now(timezone.utc),
        )
    )


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "db.sqlite")


def test_insufficient_data_keeps_current(store):
    store.ensure_project("p", threshold=None, target_miss_rate=None, audit_rate=None)
    for _ in range(10):
        add_item(store, confidence=0.3, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)

    result = recommend_threshold(store, "p")
    assert result.reason == "insufficient_data"
    assert result.current == 0.8  # DEFAULT_THRESHOLD, project.threshold unset
    assert result.recommended == result.current
    assert result.n_samples == 10
    assert result.est_review_rate is None
    assert result.est_miss_rate is None
    assert result.miss_rate_upper is None


def test_all_correct_recommends_minimal_threshold_without_clamp(store):
    store.ensure_project("p", threshold=0.05, target_miss_rate=0.05, audit_rate=0.02)
    for _ in range(80):
        add_item(store, confidence=0.02, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)

    result = recommend_threshold(store, "p")
    assert result.reason == "ok"
    assert result.recommended == 0.0
    assert result.est_miss_rate == 0.0
    assert result.miss_rate_upper <= 0.05


def test_large_change_is_clamped_to_point_one(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    for _ in range(50):
        add_item(store, confidence=0.02, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)

    result = recommend_threshold(store, "p")
    assert result.reason == "clamped"
    assert result.recommended == pytest.approx(0.7)
    assert abs(result.recommended - result.current) <= 0.1 + 1e-9


def test_no_threshold_meets_target_moves_toward_one(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    for _ in range(29):
        add_item(store, confidence=0.1, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)
    # a single audited sample at the top of the confidence range, judged wrong:
    # it survives in the "above threshold" group for every candidate t, so no
    # threshold can push the (IPW-weighted) miss rate under the target.
    add_item(store, confidence=1.0, routed_by=RoutedBy.audit, action=ReviewAction.reject)

    result = recommend_threshold(store, "p")
    assert result.reason == "no_threshold_meets_target"
    assert result.recommended == pytest.approx(0.9)  # current + max step, clamped to <=1.0
    assert result.miss_rate_upper > 0.05


def test_judge_correct_inferred_from_action_when_absent(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    add_item(
        store,
        confidence=0.3,
        routed_by=RoutedBy.low_conf,
        action=ReviewAction.edit,
        judge_correct=None,
    )
    [(item, review)] = store.list_reviewed("p")
    samples = _collect_samples(store, "p", threshold=0.8, audit_rate=0.02)
    assert samples[0].judge_correct is False  # edit -> inferred incorrect


def test_skip_action_excluded(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    add_item(store, confidence=0.3, routed_by=RoutedBy.low_conf, action=ReviewAction.skip)
    samples = _collect_samples(store, "p", threshold=0.8, audit_rate=0.02)
    assert samples == []


def test_manual_items_excluded(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    add_item(store, confidence=0.3, routed_by=RoutedBy.manual, action=ReviewAction.approve)
    samples = _collect_samples(store, "p", threshold=0.8, audit_rate=0.02)
    assert samples == []


def test_null_confidence_excluded(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    add_item(store, confidence=None, routed_by=RoutedBy.audit, action=ReviewAction.approve)
    samples = _collect_samples(store, "p", threshold=0.8, audit_rate=0.02)
    assert samples == []


def test_ipw_weighting_corrects_selection_bias(store):
    # 27 below-threshold items are deterministically captured (weight 1 each);
    # 3 above-threshold items only exist because they were audit-sampled at
    # audit_rate=0.1, so each represents ~10 actual calls.
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.1)
    for _ in range(27):
        add_item(store, confidence=0.3, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)
    for _ in range(3):
        add_item(store, confidence=0.9, routed_by=RoutedBy.audit, action=ReviewAction.approve)

    samples = _collect_samples(store, "p", threshold=0.8, audit_rate=0.1)
    weights = sorted(s.weight for s in samples)
    assert weights == [1.0] * 27 + [10.0] * 3

    total_weight = sum(s.weight for s in samples)
    review_rate, _, _ = _evaluate(samples, 0.5, total_weight)
    # naive unweighted ratio would be 27/30 = 0.9; IPW correctly discounts it
    # because the above-threshold population is undersampled by audit_rate.
    assert review_rate == pytest.approx(27 / 57)
    assert review_rate < 27 / 30


def test_apply_tuning_records_history_and_updates_threshold(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    for _ in range(50):
        add_item(store, confidence=0.02, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)

    result = apply_tuning(store, "p")
    assert result.recommended == pytest.approx(0.7)
    history = store.list_threshold_history("p")
    assert len(history) == 1
    assert history[0].old == 0.8
    assert history[0].new == pytest.approx(0.7)
    assert store.get_project("p").threshold == pytest.approx(0.7)


def test_apply_tuning_is_noop_when_insufficient_data(store):
    store.ensure_project("p", threshold=0.8, target_miss_rate=0.05, audit_rate=0.02)
    for _ in range(5):
        add_item(store, confidence=0.3, routed_by=RoutedBy.low_conf, action=ReviewAction.approve)

    result = apply_tuning(store, "p")
    assert result.reason == "insufficient_data"
    assert store.list_threshold_history("p") == []
    assert store.get_project("p").threshold == 0.8


def test_unknown_project_raises(store):
    with pytest.raises(ValueError):
        recommend_threshold(store, "does-not-exist")
