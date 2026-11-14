from .context import init, list_items, set_fields, submit
from .decorator import review
from .fields import Field
from .types import Item, Judgment, Review, ReviewAction, RoutedBy, Status, ThresholdHistory

__all__ = [
    "init",
    "review",
    "submit",
    "list_items",
    "set_fields",
    "Field",
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


def export_to(
    path,
    *,
    format="jsonl",
    project=None,
    statuses=(Status.approved, Status.corrected),
    include_images=True,
):
    """Write an export file (jsonl, openai-ft, eval-set or hf Parquet) like the CLI.

    Returns the number of records written. ``format='hf'`` needs the ``[hf]`` extra.
    """
    from .context import get_client
    from .exporters import export_to as _export_to

    client = get_client()
    if client is None:
        raise RuntimeError("Call hil.init() before exporting")
    return _export_to(
        client.store,
        project or client.project,
        path,
        format=format,
        statuses=statuses,
        include_images=include_images,
    )


__all__.append("export_to")
