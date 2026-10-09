from __future__ import annotations

import hashlib
import html
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Iterable

from bs4 import BeautifulSoup


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    soup = BeautifulSoup(html.unescape(str(value)), "lxml")
    return " ".join(soup.get_text(" ").split()).strip()


def normalized_words(value: str) -> str:
    text = clean_text(value).lower()
    text = "".join(character if character.isalnum() else " " for character in text)
    return " ".join(text.split())


def normalize_title(value: str) -> str:
    return normalized_words(value)


def normalize_venue_text(value: str) -> str:
    return normalized_words(value)


def normalize_doi(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://dx.doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().lower()
    return text or None


@dataclass(slots=True)
class Venue:
    name: str
    abbr: str
    type: str
    category: str
    tier: int = 1
    aliases: list[str] = field(default_factory=list)

    def all_names(self) -> list[str]:
        return [self.name, self.abbr, *self.aliases]


@dataclass(slots=True)
class Paper:
    title: str
    authors: list[str]
    venue: str
    venue_type: str
    category: str
    publication_date: date | None
    doi: str | None = None
    url: str | None = None
    abstract: str | None = None
    sources: set[str] = field(default_factory=set)
    date_is_exact: bool = True
    score: float = 0.0
    key: str = ""

    def year(self) -> int | None:
        return self.publication_date.year if self.publication_date else None

    def compute_key(self) -> str:
        if self.doi:
            return "doi:" + self.doi
        digest = hashlib.sha1(
            (normalize_title(self.title) + "|" + str(self.year() or "unknown")).encode("utf-8")
        ).hexdigest()
        return "title:" + digest[:20]

    def merge(self, other: "Paper") -> None:
        if not self.doi and other.doi:
            self.doi = other.doi
        if not self.url and other.url:
            self.url = other.url
        if other.abstract and (not self.abstract or len(other.abstract) > len(self.abstract)):
            self.abstract = other.abstract
        if len(other.authors) > len(self.authors):
            self.authors = list(other.authors)
        if self.publication_date is None and other.publication_date:
            self.publication_date = other.publication_date
            self.date_is_exact = other.date_is_exact
        elif other.publication_date and other.date_is_exact and not self.date_is_exact:
            self.publication_date = other.publication_date
            self.date_is_exact = True
        elif self.publication_date and other.publication_date and self.date_is_exact and other.date_is_exact:
            self.publication_date = min(self.publication_date, other.publication_date)
        self.sources.update(other.sources)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = sorted(self.sources)
        data["publication_date"] = self.publication_date.isoformat() if self.publication_date else None
        return data


class VenueRegistry:
    def __init__(self, venues: Iterable[Venue]):
        self.venues = list(venues)

    def match(self, *values: str | None) -> Venue | None:
        for raw_value in values:
            if not raw_value:
                continue
            value = normalize_venue_text(raw_value)
            if not value:
                continue
            tokens = set(value.split())
            for venue in self.venues:
                for alias in venue.all_names():
                    candidate = normalize_venue_text(alias)
                    if not candidate:
                        continue
                    if candidate == value:
                        return venue
                    if len(candidate.split()) == 1 and len(candidate) <= 5:
                        if candidate in tokens:
                            return venue
                    elif len(candidate) >= 6 and candidate in value:
                        return venue
        return None


def build_registry(items: Iterable[dict[str, Any]]) -> VenueRegistry:
    venues = []
    for item in items:
        venues.append(
            Venue(
                name=item["name"],
                abbr=item.get("abbr", item["name"]),
                type=item.get("type", "journal"),
                category=item.get("category", "general"),
                tier=int(item.get("tier", 1)),
                aliases=list(item.get("aliases", [])),
            )
        )
    return VenueRegistry(venues)


def source_config(settings: dict[str, Any], name: str) -> dict[str, Any]:
    value = settings.get("sources", {}).get(name, {})
    if isinstance(value, bool):
        return {"enabled": value}
    if isinstance(value, dict):
        return value
    return {"enabled": False}
