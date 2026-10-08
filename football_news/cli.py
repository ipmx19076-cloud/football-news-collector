"""Command line interface: `python -m football_news <command>`."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import Config, ConfigError, load_config
from .digest import write_digest
from .logging_setup import setup_logging
from .net import PoliteClient
from .normalize import utc_now
from .pipeline import SourceReport, collect, run
from .storage import Store
from .tagging import Tagger

log = logging.getLogger(__name__)

CSV_COLUMNS = ["id", "published_at", "date_inferred", "headline", "source", "url", "summary",
               "leagues", "clubs", "also_reported_by", "fetched_at"]


def _day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _today() -> str:
    return utc_now().date().isoformat()


def _print_table(reports: list[SourceReport]) -> None:
    header = f"{'source':28} {'status':12} {'fetched':>7} {'new':>5} {'dupes':>5} {'invalid':>7} {'reqs':>4}  note"
    print("\n" + header + "\n" + "-" * len(header))
    for r in reports:
        print(
            f"{r.name[:28]:28} {r.status:12} {r.fetched:>7} {r.new:>5} {r.duplicates:>5} "
            f"{r.invalid:>7} {r.requests:>4}  {r.message[:70]}"
        )


def _write_digest(config: Config, tagger: Tagger, store: Store, day: str) -> Path:
    digest_cfg = config.raw.get("digest") or {}
    path = write_digest(store, day, Path(digest_cfg.get("output_dir", "data/digests")),
                        tagger.league_order, tagger.default_tag)
    log.info("Digest written to %s", path)
    return path


def cmd_preview(config: Config, tagger: Tagger, args: argparse.Namespace) -> int:
    """Fetch, normalize and tag without storing; print sample records per source."""
    with PoliteClient(config.network) as client:
        reports = collect(config, client)
    for r in reports:
        print(f"\n=== {r.name} [{r.status}] {r.message}")
        for item in r.items[: args.limit]:
            print(json.dumps(asdict(tagger.apply(item)), ensure_ascii=False, indent=2))
    _print_table(reports)
    return 0


def cmd_run(config: Config, tagger: Tagger, args: argparse.Namespace) -> int:
    """Fetch all enabled sources, tag, deduplicate, store, write the digest.
    Exit 1 only if every attempted source failed."""
    with PoliteClient(config.network) as client, Store(config.db_path) as store:
        reports = run(config, client, store, tagger)
        totals = (store.count("items"), store.count("duplicates"))
        digest_path = None
        if (config.raw.get("digest") or {}).get("enabled", False) and not args.no_digest:
            digest_path = _write_digest(config, tagger, store, _today())
    _print_table(reports)
    print(f"\nDatabase: {config.db_path}  items={totals[0]}  duplicate sightings={totals[1]}")
    if digest_path:
        print(f"Digest:   {digest_path}")
    attempted = [r for r in reports if r.status != "skipped"]
    if attempted and all(r.status != "ok" for r in attempted):
        print("Every source failed; see log for details.", file=sys.stderr)
        return 1
    return 0


def cmd_digest(config: Config, tagger: Tagger, args: argparse.Namespace) -> int:
    """(Re)write the digest for a day from the database, without fetching."""
    with Store(config.db_path) as store:
        print(_write_digest(config, tagger, store, args.date or _today()))
    return 0


def cmd_retag(config: Config, tagger: Tagger, args: argparse.Namespace) -> int:
    """Re-apply the current keyword map to every stored item."""
    with Store(config.db_path) as store, store.transaction():
        rows = store.items_for_tagging()
        for row in rows:
            store.replace_tags(row["id"], *tagger.tag_text(row["headline"], row["summary"]))
    log.info("Re-tagged %d items", len(rows))
    return 0


def _csv_safe(value: object) -> object:
    """Stop spreadsheet apps from treating a cell as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "@", "\t", "\r"):
        return "'" + value
    return value


def cmd_export(config: Config, tagger: Tagger, args: argparse.Namespace) -> int:
    """Export items (all, or published on --date) to CSV."""
    with Store(config.db_path) as store:
        rows = store.items(args.date)
    out = Path(args.out) if args.out else config.export_dir / f"football_news_{args.date or 'all'}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_safe(row.get(k) or ("" if k != "date_inferred" else 0))
                             for k in CSV_COLUMNS})
    print(f"Wrote {len(rows)} rows to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="football_news", description=__doc__)
    parser.add_argument("--config", help="path to config.yaml (default: packaged config)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preview", help="fetch + normalize + tag without storing; show samples")
    p.add_argument("--limit", type=int, default=2, help="sample records per source")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("run", help="fetch, tag, deduplicate and store all enabled sources")
    p.add_argument("--no-digest", action="store_true", help="skip writing today's digest")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("digest", help="write the Markdown digest for a UTC day (default: today)")
    p.add_argument("--date", type=_day, help="YYYY-MM-DD (UTC)")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("retag", help="re-apply the keyword map to all stored items")
    p.set_defaults(func=cmd_retag)

    p = sub.add_parser("export", help="export items to CSV")
    p.add_argument("--date", type=_day, help="only items published on this UTC day (YYYY-MM-DD)")
    p.add_argument("--out", help="output path (default: <export_dir>/football_news_<date|all>.csv)")
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        tagger = Tagger.from_config(config.raw.get("tagging"))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    setup_logging(config.log_dir, config.log_level)
    return args.func(config, tagger, args)
