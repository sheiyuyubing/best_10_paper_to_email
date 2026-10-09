from __future__ import annotations

import argparse
import json
import logging.handlers
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml
from dotenv import load_dotenv

from .models import Paper, build_registry, source_config
from .sources import (
    enrich_arxiv,
    fetch_crossref,
    fetch_dblp,
    fetch_openalex,
    fetch_proceedings,
    arxiv_index,
)
from .storage import PaperStore
from .emailer import send_digest

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("paper_pipeline")
EXCLUDES = ("editorial", "poster", "demo", "companion", "tutorial", "workshop", "abstract")


def load_settings(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def deduplicate(papers: Iterable[Paper]) -> list[Paper]:
    output: list[Paper] = []
    by_doi: dict[str, Paper] = {}
    by_title: dict[str, Paper] = {}
    for paper in papers:
        if not paper.title:
            continue
        title_key = "".join(character if character.isalnum() else " " for character in paper.title.lower())
        title_key = " ".join(title_key.split())
        target = by_doi.get(paper.doi) if paper.doi else None
        if target is None:
            target = by_title.get(title_key)
        if target is None:
            target = paper
            output.append(target)
        else:
            target.merge(paper)
        if target.doi:
            by_doi[target.doi] = target
        target_key = "".join(character if character.isalnum() else " " for character in target.title.lower())
        by_title[" ".join(target_key.split())] = target
    for paper in output:
        paper.key = paper.compute_key()
    return output


def within_window(paper: Paper, start: date, end: date) -> bool:
    if paper.publication_date is None:
        return False
    if paper.date_is_exact:
        return start <= paper.publication_date <= end
    return paper.publication_date.year in {start.year, end.year}


def is_research(paper: Paper) -> bool:
    text = (paper.venue_type or "").lower()
    return not any(marker in text for marker in EXCLUDES)


def score(paper: Paper, tier: int, end: date) -> float:
    tier_score = {1: 60.0, 2: 45.0, 3: 30.0}.get(tier, 20.0)
    age = max(0, (end - paper.publication_date).days) if paper.publication_date else 180
    recency = max(0.0, 20.0 * (1.0 - age / 180.0))
    metadata = sum((bool(paper.doi), bool(paper.authors), bool(paper.abstract), bool(paper.url))) * 2.5
    date_score = 5.0 if paper.date_is_exact else 0.0
    return tier_score + recency + metadata + date_score


def make_session() -> requests.Session:
    session = requests.Session()
    contact = os.getenv("OPENALEX_EMAIL", "")
    session.headers.update({
        "User-Agent": "PaperDigest/1.0 (mailto:" + contact + ")",
        "Accept": "application/json, application/atom+xml, text/html;q=0.9",
    })
    return session


def run(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    settings = load_settings(args.config)
    defaults = settings.get("defaults", {})
    registry = build_registry(settings.get("venues", []))
    if not registry.venues:
        raise RuntimeError("No venues configured.")
    end = date.today()
    lookback = int(args.lookback_days or defaults.get("lookback_days", 180))
    start = end - timedelta(days=lookback - 1)
    limit = int(args.limit or defaults.get("target_count", 10))
    state_store = PaperStore(args.db)
    if not args.force:
        last_success = state_store.last_success_at()
        interval = timedelta(days=int(defaults.get("interval_days", 10)))
        if last_success and datetime.now(timezone.utc) - last_success < interval:
            print("距离上次成功发送不足 10 天，本次跳过。")
            return 0
    session = make_session()
    collected: list[Paper] = []

    if source_config(settings, "openalex").get("enabled", True):
        collected.extend(fetch_openalex(session, registry, source_config(settings, "openalex"), defaults, start, end))
    if source_config(settings, "crossref").get("enabled", True):
        collected.extend(fetch_crossref(session, registry, source_config(settings, "crossref"), defaults, start, end))
    if source_config(settings, "dblp").get("enabled", True):
        collected.extend(fetch_dblp(session, registry, source_config(settings, "dblp"), defaults, start, end))
    proceedings = settings.get("sources", {}).get("proceedings", [])
    if isinstance(proceedings, list):
        collected.extend(fetch_proceedings(session, proceedings, registry, defaults, start, end))
    if source_config(settings, "arxiv").get("enabled", True):
        enrich_arxiv(collected, arxiv_index(session, source_config(settings, "arxiv"), defaults, start, end))

    deduplicated = deduplicate(collected)
    eligible: list[Paper] = []
    for paper in deduplicated:
        venue = registry.match(paper.venue)
        if not venue or not is_research(paper) or not within_window(paper, start, end):
            continue
        paper.venue = venue.name
        paper.venue_type = venue.type
        paper.category = venue.category
        paper.score = score(paper, venue.tier, end)
        eligible.append(paper)
    eligible.sort(key=lambda paper: (paper.score, paper.publication_date or date.min, paper.title), reverse=True)

    store = PaperStore(args.db)
    sent = store.sent_keys()
    selected = [paper for paper in eligible if paper.key not in sent][:limit]
    store.save(eligible)

    output_path = ROOT / "data" / ("selected_" + end.strftime("%Y%m%d") + ".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps([paper.as_dict() for paper in selected], ensure_ascii=False, indent=2), encoding="utf-8")

    if selected and not args.dry_run:
        send_digest(selected, ROOT / "templates")
        store.mark_sent(paper.key for paper in selected)
        store.mark_success()
        print("邮件发送成功")
    elif not selected:
        print("没有可发送论文")
    else:
        print("dry-run 模式：未发送邮件")

    LOGGER.info("collected=%s deduplicated=%s eligible=%s selected=%s", len(collected), len(deduplicated), len(eligible), len(selected))
    print("候选文件：" + str(output_path))
    print("入选论文数量：" + str(len(selected)))
    for index, paper in enumerate(selected, 1):
        published = paper.publication_date.isoformat() if paper.publication_date else "unknown"
        print(str(index) + ". " + paper.title + " | " + paper.venue + " | " + published)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Fetch, deduplicate, rank, and select recent top-venue papers.")
    result.add_argument("--config", type=Path, default=ROOT / "config" / "venues.yaml")
    result.add_argument("--db", type=Path, default=ROOT / "data" / "papers.db")
    result.add_argument("--limit", type=int, default=None)
    result.add_argument("--lookback-days", type=int, default=None)
    result.add_argument("--debug", action="store_true")
    result.add_argument("--force", action="store_true", help="Ignore the 10-day interval guard.")
    result.add_argument("--dry-run", action="store_true", help="Only fetch and select papers; do not send email.")
    return result


def main() -> int:
    args = parser().parse_args()
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "paper_digest.log", when="D", interval=10, backupCount=6, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        handlers=[console_handler, file_handler],
        force=True,
    )
    try:
        return run(args)
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())


