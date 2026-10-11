"""Tests for Metadata API enrichment and fallback mechanisms."""

from unittest.mock import MagicMock, patch
import pytest
from organizador_pdf.metadata_api import MetadataEnricher
from organizador_pdf.models import (
    JevValidationResult,
    ExtractedCandidates,
)


def test_crossref_doi_fetch():
    enricher = MetadataEnricher()
    mock_item = {
        "title": ["A Gentle Introduction to Transformers"],
        "subtitle": ["From Attention to Modern LLMs"],
        "author": [{"family": "Vaswani", "given": "Ashish"}],
        "publisher": "Elsevier",
        "container-title": ["Journal of AI Research"],
        "volume": "42",
        "issue": "1",
        "page": "100-120",
        "DOI": "10.1016/j.jair.2023.01.001",
        "published": {"date-parts": [[2023, 5]]},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": mock_item}

    with patch.object(enricher.session, "get", return_value=mock_resp):
        res = enricher.fetch_crossref_by_doi("10.1016/j.jair.2023.01.001")

    assert res is not None
    assert res["title"] == "A Gentle Introduction to Transformers"
    assert res["subtitle"] == "From Attention to Modern LLMs"
    assert res["authors"] == ["VASWANI, Ashish"]
    assert res["journal"] == "Journal of AI Research"
    assert res["year"] == 2023


def test_google_books_isbn_fetch():
    enricher = MetadataEnricher()
    mock_volume_info = {
        "title": "Design Patterns",
        "subtitle": "Elements of Reusable Object-Oriented Software",
        "authors": ["Erich Gamma", "Richard Helm", "Ralph Johnson", "John Vlissides"],
        "publisher": "Addison-Wesley",
        "publishedDate": "1994-10-21",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780201633610"}],
        "pageCount": 395,
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [{"volumeInfo": mock_volume_info}]}

    with patch.object(enricher.session, "get", return_value=mock_resp):
        res = enricher.fetch_google_books_by_isbn("9780201633610")

    assert res is not None
    assert res["title"] == "Design Patterns"
    assert len(res["authors"]) == 4
    assert res["publisher"] == "Addison-Wesley"
    assert res["year"] == 1994


def test_metadata_enrichment_with_high_confidence_doi():
    enricher = MetadataEnricher()
    jev_result = JevValidationResult(
        classification="artigo_cientifico",
        classification_confidence=0.98,
        probabilities={"doi": 0.99, "isbn": 0.0, "title": 0.95},
        candidates=ExtractedCandidates(
            doi="10.1016/j.mock.2024",
            raw_title="Initial Paper Title",
        ),
    )

    mock_crossref_data = {
        "title": "Refined Paper Title from Crossref",
        "subtitle": "A Deep Dive",
        "authors": ["SMITH, John", "DOE, Jane"],
        "publisher": "Springer",
        "journal": "Nature Machine Intelligence",
        "year": 2024,
        "volume": "6",
        "pages": "10-25",
        "doi": "10.1016/j.mock.2024",
    }

    with patch.object(enricher, "fetch_crossref_by_doi", return_value=mock_crossref_data), \
         patch.object(enricher, "fetch_openalex_by_doi", return_value=None):
        metadata = enricher.enrich(jev_result)

    assert metadata.title == "Refined Paper Title from Crossref"
    assert metadata.journal == "Nature Machine Intelligence"
    assert metadata.year == 2024
    assert "Crossref (DOI)" in metadata.source_apis


def test_metadata_enrichment_fallback_by_title_when_no_identifier():
    """Verify fallback search by title is triggered when no identifier succeeds."""
    enricher = MetadataEnricher()
    jev_result = JevValidationResult(
        classification="livro",
        classification_confidence=0.80,
        probabilities={"doi": 0.10, "isbn": 0.10, "title": 0.90},
        candidates=ExtractedCandidates(
            raw_title="Clean Code",
            isbn=None,
        ),
    )

    mock_google_books_data = {
        "title": "Clean Code",
        "subtitle": "A Handbook of Agile Software Craftsmanship",
        "authors": ["Robert C. Martin"],
        "publisher": "Prentice Hall",
        "year": 2008,
        "isbn": "9780132350884",
    }

    with patch.object(enricher, "fetch_google_books_by_title", return_value=mock_google_books_data):
        metadata = enricher.enrich(jev_result)

    assert metadata.title == "Clean Code"
    assert metadata.subtitle == "A Handbook of Agile Software Craftsmanship"
    assert metadata.publisher == "Prentice Hall"
    assert metadata.year == 2008
    assert "Google Books (Title Search)" in metadata.source_apis


def test_filter_institutional_authors():
    from organizador_pdf.metadata_api import filter_institutional_authors

    raw_authors = [
        "Universidad de São Paulo",
        "Paulo Antonio de Campos Beer",
        "Wilson de Albuquerque Cavalcanti Franco",
        "Universidad de São Paulo",
    ]
    filtered = filter_institutional_authors(raw_authors)
    assert len(filtered) == 2
    assert "Universidad de São Paulo" not in filtered
    assert "Paulo Antonio de Campos Beer" in filtered
    assert "Wilson de Albuquerque Cavalcanti Franco" in filtered


def test_clean_journal_name():
    from organizador_pdf.metadata_api import clean_journal_name

    assert clean_journal_name("Revista Affectio Societatis/Affectio Societatis") == "Affectio Societatis"
    assert clean_journal_name("Journal of Machine Learning") == "Journal of Machine Learning"
    assert clean_journal_name(None) is None


def test_brasil_api_isbn_fetch():
    enricher = MetadataEnricher()
    mock_item = {
        "title": "O PACTO DA BRANQUITUDE",
        "subtitle": None,
        "authors": ["Cida Bento"],
        "publisher": "Companhia Digital",
        "year": 2022,
        "location": "São Paulo",
        "isbn": "9786557824641",
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_item

    with patch.object(enricher.session, "get", return_value=mock_resp):
        res = enricher.fetch_brasil_api_isbn("9786557824641")

    assert res is not None
    assert res["title"] == "O PACTO DA BRANQUITUDE"
    assert res["authors"] == ["Cida Bento"]
    assert res["publisher"] == "Companhia Digital"
    assert res["city"] == "São Paulo"
    assert res["year"] == 2022
