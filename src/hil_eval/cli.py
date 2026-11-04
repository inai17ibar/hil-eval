"""Optional command-line interface (install hil-eval[cli])."""

from __future__ import annotations

import ipaddress
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    import typer
else:
    try:
        import typer
    except ImportError:
        typer = None

from .exporters import export_items, write_jsonl
from .store.sqlite import SQLiteStore
from .tuning import apply_tuning, recommend_threshold
from .types import Status

app = typer.Typer(no_args_is_help=True) if typer is not None else None


if app is not None:

    @app.callback()
    def configure(
        ctx: typer.Context,
        db: str = typer.Option(".hil/hil.db", envvar="HIL_DB", help="SQLite database path."),
    ):
        ctx.obj = db

    def _serve(db, host, port, api_key, no_ui):
        import uvicorn

        from .server.app import create_app

        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback and not api_key:
            typer.echo("Warning: non-loopback host without an API key.", err=True)
        static = Path(__file__).parent / "static"
        application = create_app(SQLiteStore(db), api_key=api_key, static_dir=static)
        if no_ui:
            application.router.routes[:] = [
                r
                for r in application.router.routes
                if getattr(r, "path", "") not in {"/{full_path:path}", "/assets"}
            ]
        uvicorn.run(application, host=host, port=port)

    @app.command()
    def serve(
        ctx: typer.Context,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str | None = typer.Option(None, envvar="HIL_API_KEY"),
        no_ui: bool = False,
        db: str | None = typer.Option(None, help="Override the global database path."),
    ):
        """Serve the review UI and HTTP API."""
        _serve(db or ctx.obj, host, port, api_key, no_ui)

    @app.command()
    def ui(
        ctx: typer.Context,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str | None = typer.Option(None, envvar="HIL_API_KEY"),
        no_browser: bool = False,
        db: str | None = None,
    ):
        """Serve the UI and open a browser."""
        if not no_browser:
            webbrowser.open(f"http://{host}:{port}")
        _serve(db or ctx.obj, host, port, api_key, False)

    @app.command("export")
    def export_command(
        ctx: typer.Context,
        project: str = typer.Option(...),
        fmt: str = typer.Option("jsonl", "--format"),
        status: Annotated[list[Status] | None, typer.Option("--status")] = None,
        output: str = typer.Option("-", "--output", "-o"),
        no_images: bool = False,
    ):
        """Export reviewed items as JSONL."""
        if fmt not in {"jsonl", "openai-ft", "eval-set"}:
            raise typer.BadParameter("Choose jsonl, openai-ft, or eval-set", param_hint="--format")
        write_jsonl(
            export_items(
                SQLiteStore(ctx.obj),
                project,
                fmt=fmt,
                statuses=status if status is not None else [Status.approved, Status.corrected],
                include_images=not no_images,
            ),
            output,
        )

    @app.command()
    def tune(
        ctx: typer.Context,
        project: str = typer.Option(...),
        target_miss_rate: float | None = typer.Option(None, min=0, max=1),
        apply: bool = False,
    ):
        """Recommend a threshold; optionally persist it."""
        try:
            result = (apply_tuning if apply else recommend_threshold)(
                SQLiteStore(ctx.obj), project, target_miss_rate=target_miss_rate
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo("current\trecommended\tn_samples\treview rate\tmiss rate\tupper bound\treason")
        typer.echo(
            "\t".join(
                str(v) if v is not None else "-"
                for v in (
                    result.current,
                    result.recommended,
                    result.n_samples,
                    result.est_review_rate,
                    result.est_miss_rate,
                    result.miss_rate_upper,
                    result.reason,
                )
            )
        )

    @app.command()
    def projects(ctx: typer.Context):
        """List projects and their configuration."""
        typer.echo("project\tthreshold\ttarget miss rate\taudit rate")
        for p in SQLiteStore(ctx.obj).list_projects():
            typer.echo(f"{p.name}\t{p.threshold}\t{p.target_miss_rate}\t{p.audit_rate}")

    @app.command()
    def history(ctx: typer.Context, project: str = typer.Option(...)):
        """Show threshold history."""
        typer.echo("created_at\told\tnew\tn_samples\treview rate\tmiss rate")
        for h in SQLiteStore(ctx.obj).list_threshold_history(project):
            typer.echo(
                f"{h.created_at.isoformat()}\t{h.old}\t{h.new}\t{h.n_samples}"
                f"\t{h.est_review_rate}\t{h.est_miss_rate}"
            )


def main():
    if app is None:
        raise SystemExit("CLI dependencies missing; pip install hil-eval[cli]")
    app()
