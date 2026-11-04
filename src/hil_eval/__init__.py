from .context import init, list_items, submit
from .decorator import review
from .types import Item, Judgment, Review, ReviewAction, RoutedBy, Status, ThresholdHistory

__all__ = [
    "init",
    "review",
    "submit",
    "list_items",
    "Judgment",
    "Item",
    "Status",
    "RoutedBy",
    "Review",
    "ReviewAction",
    "ThresholdHistory",
]


def export(
    *, project=None, fmt="jsonl", statuses=(Status.approved, Status.corrected), include_images=True
):
    """Iterate exported reviews from the default client's store/project."""
    from .context import get_client
    from .exporters import export_items

    client = get_client()
    if client is None:
        raise RuntimeError("Call hil.init() before exporting")
    return export_items(
        client.store,
        project or client.project,
        fmt=fmt,
        statuses=statuses,
        include_images=include_images,
    )


__all__.append("export")
