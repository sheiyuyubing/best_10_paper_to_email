from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Iterable

import feedparser
import requests
from bs4 import BeautifulSoup

from .models import Paper, VenueRegistry, clean_text, normalize_doi, normalize_title


class RateLimiter:
    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self.last_request = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self.last_request
        remaining = self.delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()


def request_json(session: requests.Session, url: str, params: dict[str, Any], limiter: RateLimiter,
                 timeout: int, retries: int) -> dict[str, Any]:
    for attempt in range(1, retries + 1):
        limiter.wait()
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt >= retries:
                print("Request failed: " + url + " | " + str(exc))
                return {}
            time.sleep(2 ** (attempt - 1))
    return {}


def request_text(session: requests.Session, url: str, params: dict[str, Any], limiter: RateLimiter,
                 timeout: int, retries: int) -> str:
    for attempt in range(1, retries + 1):
        limiter.wait()
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            if attempt >= retries:
                print("Request failed: " + url + " | " + str(exc))
                return ""
            time.sleep(2 ** (attempt - 1))
    return ""


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    separator = "-" if "-" in text else "/"
    parts = text.split(separator)
    if len(parts) >= 3 and parts[0].isdigit():
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None
    if len(text) == 4 and text.isdigit():
        return date(int(text), 1, 1)
    return None


def date_from_parts(value: Any) -> date | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not parts[0]:
        return None
    numbers = list(parts[0])
    while len(numbers) < 3:
        numbers.append(1)
    try:
        return date(int(numbers[0]), int(numbers[1]), int(numbers[2]))
    except (TypeError, ValueError):
        return None


def first_crossref_date(item: dict[str, Any]) -> date | None:
    for key in ("published-online", "published-print", "published", "issued", "created"):
        parsed = date_from_parts(item.get(key))
        if parsed:
            return parsed
    return None


def inverted_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions = []
    for word, locations in value.items():
        if isinstance(locations, list):
            for location in locations:
                if isinstance(location, int):
                    positions.append((location, str(word)))
    return " ".join(word for _, word in sorted(positions)).strip()


def fetch_openalex(session: requests.Session, registry: VenueRegistry, config: dict[str, Any],
                   defaults: dict[str, Any], start: date, end: date) -> list[Paper]:
    papers: list[Paper] = []
    cursor = "*"
    limiter = RateLimiter(float(defaults.get("polite_delay_seconds", 1.0)))
    filter_text = "from_publication_date:" + start.isoformat() + ",to_publication_date:" + end.isoformat() + ",type:article"
    for _ in range(int(defaults.get("max_pages_per_source", 5))):
        payload = request_json(
            session,
            "https://api.openalex.org/works",
            {
                "filter": filter_text,
                "per-page": int(config.get("per_page", 200)),
                "cursor": cursor,
                "mailto": os.getenv("OPENALEX_EMAIL", ""),
            },
            limiter,
            int(defaults.get("request_timeout_seconds", 30)),
            int(defaults.get("max_retries", 3)),
        )
        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            break
        for item in results:
            if not isinstance(item, dict):
                continue
            location = item.get("primary_location") or {}
            source = location.get("source") or item.get("host_venue") or {}
            venue = registry.match(source.get("display_name"))
            if not venue:
                continue
            publication_date = parse_date(item.get("publication_date"))
            if not publication_date:
                continue
            authors = []
            for authorship in item.get("authorships", []):
                author = authorship.get("author") or {}
                if author.get("display_name"):
                    authors.append(str(author["display_name"]))
            doi = normalize_doi(item.get("doi"))
            url = location.get("landing_page_url") or (("https://doi.org/" + doi) if doi else None)
            papers.append(Paper(
                clean_text(item.get("display_name") or item.get("title")),
                authors,
                venue.name,
                venue.type,
                venue.category,
                publication_date,
                doi,
                url,
                inverted_abstract(item.get("abstract_inverted_index")),
                {"openalex"},
                True,
            ))
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return papers


def fetch_crossref(session: requests.Session, registry: VenueRegistry, config: dict[str, Any],
                   defaults: dict[str, Any], start: date, end: date) -> list[Paper]:
    papers: list[Paper] = []
    cursor = "*"
    limiter = RateLimiter(float(defaults.get("polite_delay_seconds", 1.0)))
    filter_text = "from-pub-date:" + start.isoformat() + ",until-pub-date:" + end.isoformat() + ",type:journal-article"
    for _ in range(int(defaults.get("max_pages_per_source", 5))):
        payload = request_json(
            session,
            "https://api.crossref.org/works",
            {
                "filter": filter_text,
                "rows": int(config.get("rows", 200)),
                "cursor": cursor,
                "mailto": os.getenv("CROSSREF_MAILTO", ""),
            },
            limiter,
            int(defaults.get("request_timeout_seconds", 30)),
            int(defaults.get("max_retries", 3)),
        )
        message = payload.get("message") or {}
        items = message.get("items", [])
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            titles = item.get("title") or []
            title = clean_text(titles[0] if isinstance(titles, list) and titles else item.get("title"))
            containers = item.get("container-title") or []
            event_name = (item.get("event") or {}).get("name")
            venue = registry.match(event_name, *(containers if isinstance(containers, list) else []))
            publication_date = first_crossref_date(item)
            if not venue or not title or not publication_date:
                continue
            authors = []
            for author in item.get("author", []) or []:
                if isinstance(author, dict):
                    full_name = " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
                    if full_name:
                        authors.append(full_name)
            doi = normalize_doi(item.get("DOI"))
            papers.append(Paper(
                title,
                authors,
                venue.name,
                venue.type,
                venue.category,
                publication_date,
                doi,
                item.get("URL") or (("https://doi.org/" + doi) if doi else None),
                clean_text(item.get("abstract")),
                {"crossref"},
                True,
            ))
        cursor = message.get("next-cursor")
        if not cursor:
            break
    return papers


def fetch_dblp(session: requests.Session, registry: VenueRegistry, config: dict[str, Any],
               defaults: dict[str, Any], start: date, end: date) -> list[Paper]:
    papers: list[Paper] = []
    conference_venues = sorted(
        [venue for venue in registry.venues if venue.type == "conference"],
        key=lambda venue: venue.tier,
    )
    years = list(range(start.year, end.year + 1))
    limiter = RateLimiter(float(defaults.get("polite_delay_seconds", 1.0)))
    for venue in conference_venues[:int(config.get("max_conference_queries", 25))]:
        for year in years:
            payload = request_json(
                session,
                "https://dblp.org/search/publ/api",
                {"q": venue.abbr + " " + str(year), "h": int(config.get("per_page", 1000)), "format": "json"},
                limiter,
                int(defaults.get("request_timeout_seconds", 30)),
                int(defaults.get("max_retries", 3)),
            )
            hits = (((payload.get("result") or {}).get("hits") or {}).get("hit") or [])
            if isinstance(hits, dict):
                hits = [hits]
            for hit in hits:
                info = hit.get("info") or {}
                if str(info.get("year", "")) != str(year):
                    continue
                title = clean_text(info.get("title"))
                if not title:
                    continue
                raw_authors = (info.get("authors") or {}).get("author") or []
                if isinstance(raw_authors, dict):
                    raw_authors = [raw_authors]
                authors = []
                for author in raw_authors:
                    if isinstance(author, dict) and author.get("text"):
                        authors.append(clean_text(author["text"]))
                    elif isinstance(author, str):
                        authors.append(clean_text(author))
                doi = normalize_doi(info.get("doi"))
                ee = info.get("ee")
                if isinstance(ee, list):
                    ee = ee[0] if ee else None
                papers.append(Paper(
                    title,
                    authors,
                    venue.name,
                    venue.type,
                    venue.category,
                    date(year, 1, 1),
                    doi,
                    str(ee) if ee else None,
                    None,
                    {"dblp"},
                    False,
                ))
    return papers


def arxiv_index(session: requests.Session, config: dict[str, Any], defaults: dict[str, Any],
                start: date, end: date) -> dict[str, dict[str, str]]:
    query = "cat:cs.* AND submittedDate:[" + start.strftime("%Y%m%d") + "0000 TO " + end.strftime("%Y%m%d") + "2359]"
    text = request_text(
        session,
        "https://export.arxiv.org/api/query",
        {"search_query": query, "start": 0, "max_results": int(config.get("max_results", 500)),
         "sortBy": "submittedDate", "sortOrder": "descending"},
        RateLimiter(float(defaults.get("arxiv_delay_seconds", 3.0))),
        int(defaults.get("request_timeout_seconds", 30)),
        int(defaults.get("max_retries", 3)),
    )
    if not text:
        return {}
    feed = feedparser.parse(text)
    output = {}
    for entry in feed.entries:
        title = clean_text(entry.get("title"))
        if title:
            output[normalize_title(title)] = {"abstract": clean_text(entry.get("summary")), "url": str(entry.get("link") or "")}
    return output


def enrich_arxiv(papers: Iterable[Paper], index: dict[str, dict[str, str]]) -> None:
    for paper in papers:
        record = index.get(normalize_title(paper.title))
        if not record:
            continue
        if not paper.abstract:
            paper.abstract = record.get("abstract")
        if not paper.url:
            paper.url = record.get("url")
        paper.sources.add("arxiv")


def fetch_proceedings(session: requests.Session, sources: Iterable[dict[str, Any]], registry: VenueRegistry,
                      defaults: dict[str, Any], start: date, end: date) -> list[Paper]:
    papers: list[Paper] = []
    limiter = RateLimiter(float(defaults.get("proceedings_delay_seconds", 5.0)))
    for source in sources:
        if not source.get("enabled", True) or not source.get("url") or not source.get("item_selector"):
            continue
        page = request_text(session, source["url"], {}, limiter,
                            int(defaults.get("request_timeout_seconds", 30)),
                            int(defaults.get("max_retries", 3)))
        if not page:
            continue
        soup = BeautifulSoup(page, "lxml")
        for item in soup.select(source["item_selector"]):
            title_node = item.select_one(source.get("title_selector", "a"))
            if not title_node:
                continue
            link_node = item.select_one(source.get("link_selector", "a"))
            link = None
            if link_node and link_node.get("href"):
                link = requests.compat.urljoin(source["url"], link_node["href"])
            date_node = item.select_one(source["date_selector"]) if source.get("date_selector") else None
            publication_date = parse_date(date_node.get_text(" ") if date_node else None)
            if publication_date and not (start <= publication_date <= end):
                continue
            venue = registry.match(source.get("venue"))
            if not venue:
                continue
            papers.append(Paper(
                clean_text(title_node.get_text(" ")), [], venue.name, venue.type, venue.category,
                publication_date, None, link, None, {"proceedings"}, publication_date is not None,
            ))
    return papers

