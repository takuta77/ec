"""CLI for DLQ admin operations.

Usage:
    uv run python scripts/dlq.py count <queue>
    uv run python scripts/dlq.py peek <queue> [--limit N] [--preview-chars N]
    uv run python scripts/dlq.py redrive <queue> [--limit N | --all] [--apply]
    uv run python scripts/dlq.py drain <queue> [--limit N | --all] [--apply]

`--apply` is required to actually mutate; without it, redrive/drain run as dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import aio_pika

from app.core.config import Settings
from app.mq.dlq_admin import (
    DLQAdminError,
    DLQNotFoundError,
    count_dlq,
    drain_dlq,
    peek_dlq,
    redrive_dlq,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dlq", description="DLQ admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p_count = sub.add_parser("count", help="show DLQ message count")
    p_count.add_argument("queue", help="consumer queue name (DLQ is <queue>.dlq)")

    p_peek = sub.add_parser("peek", help="preview DLQ messages without consuming")
    p_peek.add_argument("queue")
    p_peek.add_argument("--limit", type=int, default=10)
    p_peek.add_argument("--preview-chars", type=int, default=200)

    p_redrive = sub.add_parser("redrive", help="re-publish DLQ messages to main exchange")
    p_redrive.add_argument("queue")
    grp_r = p_redrive.add_mutually_exclusive_group()
    grp_r.add_argument("--limit", type=int, default=None)
    grp_r.add_argument("--all", action="store_true")
    p_redrive.add_argument(
        "--apply", action="store_true", help="actually publish (default is dry-run)"
    )

    p_drain = sub.add_parser("drain", help="permanently discard DLQ messages")
    p_drain.add_argument("queue")
    grp_d = p_drain.add_mutually_exclusive_group()
    grp_d.add_argument("--limit", type=int, default=None)
    grp_d.add_argument("--all", action="store_true")
    p_drain.add_argument(
        "--apply", action="store_true", help="actually discard (default is dry-run)"
    )

    return parser


def _resolve_limit(args: argparse.Namespace) -> int | None:
    if getattr(args, "all", False):
        return None
    return args.limit


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]
    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    except (ConnectionError, OSError) as exc:
        print(f"error: cannot connect to {settings.rabbitmq_url}: {exc}", file=sys.stderr)
        return 3

    try:
        if args.command == "count":
            r = await count_dlq(connection=conn, queue=args.queue)
            print(f"{r.queue}: {r.message_count}")
        elif args.command == "peek":
            msgs = await peek_dlq(
                connection=conn,
                queue=args.queue,
                limit=args.limit,
                preview_chars=args.preview_chars,
            )
            print(f"{'event_id':36}  {'routing_key':30}  {'deaths':>6}  preview")
            for m in msgs:
                eid = (m.event_id or "<none>")[:36]
                rk = (m.routing_key or "<none>")[:30]
                preview = m.body_preview.replace("\n", " ")
                print(f"{eid:36}  {rk:30}  {m.death_count:>6}  {preview}")
        elif args.command == "redrive":
            r = await redrive_dlq(
                connection=conn,
                queue=args.queue,
                limit=_resolve_limit(args),
                dry_run=not args.apply,
            )
            label = "[dry-run] " if r.dry_run else ""
            print(f"{label}requested={r.requested} redriven={r.redriven} skipped={len(r.skipped)}")
            if r.skipped:
                print(f"  skipped event_ids: {', '.join(r.skipped)}", file=sys.stderr)
        elif args.command == "drain":
            r = await drain_dlq(
                connection=conn,
                queue=args.queue,
                limit=_resolve_limit(args),
                dry_run=not args.apply,
            )
            label = "[dry-run] " if r.dry_run else ""
            print(f"{label}drained={r.drained}")
        return 0
    except DLQNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DLQAdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("interrupted; rolled back in-flight messages", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
