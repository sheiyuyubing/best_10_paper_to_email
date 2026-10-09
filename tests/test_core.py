from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.emailer import render_digest
from src.models import Paper, normalize_doi
from src.pipeline import deduplicate
from src.storage import PaperStore

ROOT = Path(__file__).resolve().parents[1]


class ModelTests(unittest.TestCase):
    def test_normalize_doi(self) -> None:
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")
        self.assertEqual(normalize_doi("doi:10.1000/XYZ"), "10.1000/xyz")

    def test_stable_key_uses_doi(self) -> None:
        paper = Paper(
            title="Test Paper",
            authors=["Author"],
            venue="Journal of the ACM",
            venue_type="journal",
            category="theory",
            publication_date=date(2026, 1, 1),
            doi="10.1000/test",
        )
        self.assertEqual(paper.compute_key(), "doi:10.1000/test")


class PipelineTests(unittest.TestCase):
    def test_deduplicate_by_title(self) -> None:
        first = Paper(
            title="Same Title",
            authors=[],
            venue="Journal of the ACM",
            venue_type="journal",
            category="theory",
            publication_date=date(2026, 1, 1),
        )
        second = Paper(
            title="Same   Title",
            authors=["Author"],
            venue="Journal of the ACM",
            venue_type="journal",
            category="theory",
            publication_date=date(2026, 1, 1),
            doi="10.1000/same",
        )
        result = deduplicate([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].doi, "10.1000/same")


class StorageTests(unittest.TestCase):
    def test_metadata_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(Path(directory) / "papers.db")
            self.assertIsNone(store.last_success_at())
            store.mark_success()
            self.assertIsNotNone(store.last_success_at())


class EmailTests(unittest.TestCase):
    def test_render_digest(self) -> None:
        paper = Paper(
            title="Rendered Paper",
            authors=["Author"],
            venue="Journal of the ACM",
            venue_type="journal",
            category="theory",
            publication_date=date(2026, 1, 1),
            url="https://example.com/paper",
        )
        plain, html = render_digest([paper], ROOT / "templates")
        self.assertIn("Rendered Paper", plain)
        self.assertIn("Rendered Paper", html)


if __name__ == "__main__":
    unittest.main()
