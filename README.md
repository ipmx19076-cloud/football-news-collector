# Football news collector

Collects football headlines once a day from open RSS feeds (plus optional free-tier news APIs),
normalizes and deduplicates them into a SQLite archive, tags them by league and club, and
writes a daily Markdown digest. Runs locally with one command, or daily on GitHub Actions.

Only the headline, link and the feed/API-provided snippet are stored. Full article text is never
fetched, stored or republished.

## Sources

Verified on 2026-10-08 (HTTP 200, valid feed, robots.txt allows the feed URL):

| Source | Type | Default |
|---|---|---|
| BBC Sport Football | RSS | on |
| ESPN FC | RSS (times read as America/New_York, see `assume_timezone`) | on |
| Sky Sports Football | RSS | on |
| The Guardian Football | RSS | on |
| FourFourTwo | RSS | on |
| Mirror Football | RSS | on |
| CBS Sports Soccer | RSS | off |
| NewsAPI | API, needs `NEWSAPI_KEY` | on, skipped without key |
| GNews | API, needs `GNEWS_API_KEY` | on, skipped without key |
| Reddit r/soccer | Official OAuth API only, needs credentials | off |

Rejected candidates and the reasons are listed as comments in `football_news/config.yaml`.

## Quick start (local)

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m football_news run
```

Run commands from the repository root (relative paths in the config resolve from there).
`run` prints a per-source summary:

```
source                       status       fetched   new dupes invalid reqs  note
--------------------------------------------------------------------------------
BBC Sport Football           ok                50    50     0       0    2
...
NewsAPI                      skipped            0     0     0       0    0  $NEWSAPI_KEY is not set
```

Status is one of `ok`, `skipped` (e.g. no API key), `failed` (HTTP error, timeout, malformed
feed), `rate_limited` (HTTP 429 persisted: skipped for the day) or `disallowed` (robots.txt
forbids it: the source is not fetched; review its terms and disable it).

## Commands

| Command | What it does |
|---|---|
| `python -m football_news run` | Fetch all enabled sources, tag, deduplicate, store, write today's digest |
| `python -m football_news run --no-digest` | Same, without the digest |
| `python -m football_news preview --limit 3` | Fetch + normalize + tag, print sample records, store nothing |
| `python -m football_news digest [--date YYYY-MM-DD]` | Rewrite a day's digest from the database |
| `python -m football_news export [--date YYYY-MM-DD] [--out file.csv]` | CSV of all items, or those published on a UTC day |
| `python -m football_news retag` | Re-apply the keyword map to all stored items (after editing it) |
| `python -m football_news --config other.yaml run` | Use another config (or set `FOOTBALL_NEWS_CONFIG`) |

Exit codes: `0` success (even if some sources failed), `1` every attempted source failed,
`2` configuration error.

## Outputs

- `data/football_news.db`: SQLite. Tables: `items` (one row per story), `duplicates` (other
  sources/URLs that carried the same story), `item_tags` (league/club tags), `runs` (per-run
  summary including HTTP request counts). Open with any SQLite browser, or e.g.:

  ```sql
  SELECT published_at, source, headline, url FROM items
  WHERE id IN (SELECT item_id FROM item_tags WHERE tag = 'Arsenal')
  ORDER BY published_at DESC;
  ```

- `data/digests/YYYY-MM-DD.md`: items first collected that UTC day, grouped by league (the
  first, most specific league tag), newest first, with source, link, clubs, and "also: …" when
  the same story came from several sources.
- `data/exports/*.csv`: from `export`. UTF-8 with BOM so Excel opens it correctly.
- `logs/football_news.log`: rotating log (1 MB × 5).

All timestamps are ISO 8601 UTC (`2026-10-08T13:30:00Z`). Items whose feed gave no usable
date get the fetch time and `date_inferred = 1` (shown as "time unknown" in the digest).

## Adding or changing a source

Add an entry under `sources:` in `football_news/config.yaml`. No code changes needed:

```yaml
  - name: My Feed            # unique; shown in logs, digest and CSV
    type: rss                # rss | newsapi | gnews | reddit
    url: https://example.com/football/rss
    enabled: true
    timeout: 20              # optional, seconds (default: network.default_timeout)
    max_items: 30            # optional (default 50)
    assume_timezone: Europe/London   # optional, only for feeds that mislabel their time zone
```

Before adding a feed, check that it responds with a valid feed (`preview` will tell you) and
that the site's robots.txt and terms allow automated access. The collector checks robots.txt
itself and refuses disallowed URLs, but terms of use are your call. A new adapter *type* is a
subclass of `BaseSource` in `football_news/sources/`, registered in `sources/__init__.py`.

Set `user_agent` in the config to something that identifies you, e.g. include your
repository URL, so site operators can reach you.

## Tagging

The keyword map is under `tagging:` in the config. Matching is case- and accent-insensitive on
whole words, over the headline and summary (`match_fields`). Each league or club has
`keywords` and optional `exclude` phrases (e.g. Inter excludes "inter miami"). A club adds its
`league`. Items with no match get `Other/General`; nothing is guessed beyond the map. The order
of `leagues` is the section order in the digest. Club leagues change with promotion and
relegation, so review them each season and then run `retag`.

## Deduplication

1. **Same URL**: links are compared after removing tracking parameters (`utm_*`, `at_*`,
   `fbclid`, …), fragments, `www.`/`amp.`, scheme and trailing slash. Re-running never adds rows.
2. **Same story, different source**: headlines are normalized (lowercase, accents and
   punctuation removed, stopwords removed, light stemming) and compared with stored items from
   other sources published within ±36 h. A match needs word overlap ≥ 0.8 or character
   similarity ≥ 0.9 (exact match for headlines under 4 words). The story is kept once; the other
   source is recorded in `duplicates`. Thresholds are under `dedup:` in the config.

Stories that are the same news but written with different headlines are kept separately: the
rule matches syndicated or lightly edited headlines, not meaning.

## Daily schedule (GitHub Actions)

`.github/workflows/daily.yml` runs at **06:17 UTC** every day and on demand (Actions tab, then
"Daily football news", then "Run workflow"). Each run:

1. runs the offline tests,
2. restores `data/` from the `data` branch (`scripts/data_branch.sh restore`),
3. runs the collector,
4. publishes the database and digests back to the `data` branch,
5. uploads the log as an artifact (kept 14 days).

**Persistence:** the `data` branch holds a **single commit that is replaced each run**
(force-pushed by the workflow). The SQLite file changes daily and git stores every version of a
binary in full, so normal history would grow the repository by the database size each day.
All digests remain in the snapshot, so browse them at
`https://github.com/<you>/<repo>/tree/data/digests`. Do not commit to the `data` branch by hand.
If every source fails, the job fails and nothing is published, so the previous data is kept.

To get the latest data locally: `bash scripts/data_branch.sh restore` (overwrites `./data`).

### Secrets (all optional)

Add them under repository Settings, then Secrets and variables, then Actions. Without any
secrets, the six RSS feeds still run.

| Secret | Used by | Notes |
|---|---|---|
| `NEWSAPI_KEY` | NewsAPI | Free developer plan: 100 requests/day, intended for development use; check its terms. One request per run. |
| `GNEWS_API_KEY` | GNews | Free plan: 100 requests/day, max 10 articles per request. One request per run. |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Reddit | Create a "script" app at reddit.com/prefs/apps. Also set `enabled: true` for the Reddit source. |

For local runs, set the same names as environment variables. Keys are never written to the
config, database, digests or logs (log output is filtered for key-like values).

### Enabling it on GitHub

1. Push this project to a GitHub repository (the `data/` and `logs/` folders are gitignored).
2. Optionally add the secrets above.
3. Run the workflow once by hand from the Actions tab to create the `data` branch.
4. On repositories without recent activity, GitHub may disable scheduled workflows after 60
   days; re-enable them from the Actions tab if that happens.

## Development

```bash
python -m pytest -q
```

Tests use saved fixtures and mocked HTTP; they make no network calls.

```
football_news/
  config.yaml        sources, tagging map, dedup + digest settings
  cli.py             commands
  pipeline.py        runs each source in isolation, then tags, deduplicates, stores
  net.py             polite HTTP: User-Agent, timeouts, retries + backoff, per-host delay, robots.txt
  normalize.py       HTML stripping, dates to UTC, URL cleaning
  dedup.py           deduplication rule
  tagging.py         league/club keyword tagging
  storage.py         SQLite schema, migrations, queries
  digest.py          Markdown digest
  sources/           rss, newsapi, gnews, reddit adapters
scripts/data_branch.sh   restore/publish the data branch (used by CI)
tests/                   unit tests and fixtures
```

## Known limitations

- Headline-based dedup does not merge differently worded reports of the same news.
- Tagging is keyword-only: clubs outside the map (e.g. most Championship sides) and players are
  not tagged; a club mention in a summary can add a league that is only incidental.
- Feeds can change or disappear without notice; a failing source shows as `failed` in the run
  summary and log. Sites may also treat GitHub's data-centre IPs differently from a home
  connection.
- The free NewsAPI plan is meant for development and delays articles; GNews free returns at most
  10 articles per request.
- The digest groups by UTC day, which may differ from your local day.
