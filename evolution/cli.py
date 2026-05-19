"""CLI entry points for offline evolution jobs."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from .attribution import SkillAttributionJob
from .postmortem import PostmortemEngine


def _load_recent_traces(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT *
            FROM traces
            WHERE forward_return_30m IS NOT NULL OR pnl IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def _run_attribution(args: argparse.Namespace) -> None:
    job = SkillAttributionJob(db_path=args.db_path)
    performance = job.run(since=args.since)
    if args.confusion_matrix:
        matrix = job.aggregate_fine_regime_accuracy(
            window=args.window,
            since=args.since,
        )
        print(json.dumps(_stringify_tuple_keys(matrix), ensure_ascii=False, indent=2))
        return
    print(f"skill_performance rows: {len(performance)}")


def _run_postmortem(args: argparse.Namespace) -> None:
    traces = _load_recent_traces(args.db_path, limit=args.limit)
    engine = PostmortemEngine(dry_run=args.dry_run, db_path=args.db_path)
    drafts = engine.analyze_traces(traces)
    print(f"postmortem drafts: {len(drafts)}")


def _stringify_tuple_keys(payload: Dict[Any, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(key, tuple):
            result["|".join(str(part) for part in key)] = value
        else:
            result[str(key)] = value
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crypto Agent evolution CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    attribution = subparsers.add_parser("attribution", help="Run skill attribution")
    attribution.add_argument("--db-path", default="memory/trades.db")
    attribution.add_argument("--since", default=None)
    attribution.add_argument("--window", default="30m", choices=["5m", "30m", "4h"])
    attribution.add_argument("--confusion-matrix", action="store_true")
    attribution.set_defaults(func=_run_attribution)

    postmortem = subparsers.add_parser("postmortem", help="Run trace postmortem")
    postmortem.add_argument("--db-path", default="memory/trades.db")
    postmortem.add_argument("--limit", type=int, default=200)
    postmortem.add_argument("--dry-run", action="store_true", default=False)
    postmortem.set_defaults(func=_run_postmortem)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
