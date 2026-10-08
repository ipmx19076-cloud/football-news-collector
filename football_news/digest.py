"""Daily Markdown digest: items first collected on a UTC day, grouped by league, newest first."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .normalize import to_iso, utc_now
from .storage import Store

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")


def md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _anchor(name: str) -> str:
    """GitHub-style heading anchor."""
    return re.sub(r"[^a-z0-9 -]", "", name.lower()).replace(" ", "-")


def render_digest(rows: list[dict], day: str, league_order: list[str], default_tag: str) -> str:
    """Build the Markdown text. Each item is listed once, under its primary (first) league."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        leagues = (row.get("leagues") or default_tag).split("|")
        groups[leagues[0]].append(row)
    order = [lg for lg in league_order if lg in groups] + sorted(set(groups) - set(league_order))
    sources = {row["source"] for row in rows}

    lines = [
        f"# Football news digest: {day}",
        "",
        f"_{len(rows)} stories from {len(sources)} sources, first collected on {day} (UTC). "
        f"Generated {to_iso(utc_now())}. Times are UTC._",
        "",
    ]
    if not rows:
        lines.append("No new stories were collected on this day.")
        return "\n".join(lines) + "\n"

    lines.append("**Contents:** " + " · ".join(
        f"[{lg} ({len(groups[lg])})](#{_anchor(f'{lg} ({len(groups[lg])})')})" for lg in order))
    for league in order:
        items = sorted(groups[league], key=lambda r: r["published_at"], reverse=True)
        lines += ["", f"## {league} ({len(items)})", ""]
        for row in items:
            if row["date_inferred"]:
                when = "time unknown"
            elif row["published_at"][:10] == day:
                when = row["published_at"][11:16]
            else:
                when = f"{row['published_at'][5:10]} {row['published_at'][11:16]}"
            extras = []
            if row.get("also_reported_by"):
                extras.append("also: " + ", ".join(row["also_reported_by"].split("|")))
            other_leagues = (row.get("leagues") or "").split("|")[1:]
            if other_leagues:
                extras.append("also in " + ", ".join(other_leagues))
            if row.get("clubs"):
                extras.append(", ".join(row["clubs"].split("|")))
            extra = f" · {md_escape(' · '.join(extras))}" if extras else ""
            url = row["url"].replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            lines.append(
                f"- `{when}` [{md_escape(row['headline'])}]({url}) "
                f"({md_escape(row['source'])}){extra}"
            )
    return "\n".join(lines) + "\n"


def write_digest(store: Store, day: str, output_dir: Path, league_order: list[str],
                 default_tag: str) -> Path:
    """Write (or overwrite) data/digests/<day>.md. Safe to call repeatedly."""
    rows = store.items(day, by="fetched")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{day}.md"
    path.write_text(render_digest(rows, day, league_order, default_tag), encoding="utf-8")
    return path
