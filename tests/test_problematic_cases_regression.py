"""Regression tests for the 5 real-world problematic cases reported by the user."""

from unittest.mock import patch
import pytest
from organizador_pdf.classifier_jev import (
    extract_candidate_metadata,
    parse_page_cip,
)
from organizador_pdf.models import (
    PublicationMetadata,
    Identifiers,
    JevValidationResult,
    ExtractedCandidates,
)
from organizador_pdf.converter import generate_standardized_filename
from organizador_pdf.abnt_formatter import format_single_author_abnt, ABNTFormatter
from organizador_pdf.metadata_api import MetadataEnricher


def test_case_1_doi_enrichment_unconditional():
    """Case 1: Direct DOI lookup must enrich article metadata even if probability is below 0.95."""
    enricher = MetadataEnricher()
    jev_result = JevValidationResult(
        classification="artigo",
        classification_confidence=0.86,
        probabilities={"doi": 0.82},  # Previously skipped because < 0.95
        candidates=ExtractedCandidates(
            doi="10.17533/udea.affs.v14n27a08",
            raw_title="Revista Affectio Societatis Vol. 14, N.° 27",
            raw_authors=["BEER e FRANCO"],
        ),
    )

    mock_crossref_data = {
        "title": "Da indissociabilidade entre clínica e política em psicanálise",
        "authors": ["Paulo Antonio de Campos Beer", "Wilson de Albuquerque Cavalcanti Franco"],
        "journal": "Affectio Societatis",
        "volume": "14",
        "number": "27",
        "pages": "157-179",
        "year": 2017,
        "doi": "10.17533/udea.affs.v14n27a08",
        "issn": "0123-8884",
    }

    with patch.object(enricher, "fetch_crossref_by_doi", return_value=mock_crossref_data):
        meta = enricher.enrich(jev_result)

    assert meta.title == "Da indissociabilidade entre clínica e política em psicanálise"
    assert "Paulo Antonio de Campos Beer" in meta.authors
    assert "Crossref (DOI)" in meta.source_apis

    # Filename check
    fname = generate_standardized_filename(meta)
    assert fname.startswith("BEER, Paulo Antonio de Campos et al. - Da indissociabilidade entre clínica e política em psicanálise")


def test_case_2_ignore_rental_disclaimer_and_filter_ads():
    """Case 2: Pirate rental/commercial disclaimers must never become candidate title."""
    sample_text = (
        "DADOS DE ODINRIGHT\n"
        "Sobre a obra:\n"
        "A presente obra é disponibilizada pela equipe eLivros\n"
        "É expressamente proibida e totalmente repudíavel a venda,\n"
        "aluguel, ou quaisquer uso comercial do presente conteúdo.\n"
        "SUMÁRIO\n"
        "1. Capa\n"
        "2. Folha de rosto\n"
        "3. Introdução\n"
    )
    candidates = extract_candidate_metadata(sample_text)
    assert candidates.raw_title != "aluguel, ou quaisquer uso comercial do presente conteúdo."


def test_case_3_title_subtitle_deduplication():
    """Case 3: If subtitle is already present in title, do not duplicate in filename."""
    meta = PublicationMetadata(
        title="Neuropsicologia Teoria e Pratica",
        subtitle="teoria e prática",
        authors=["Daniel Fuentes", "et al."],
        year=2014,
        classification="livro",
    )
    fname = generate_standardized_filename(meta)
    assert fname == "FUENTES, Daniel et al. - Neuropsicologia Teoria e Pratica - (2014)"
    assert "teoria e prática - teoria e prática" not in fname


def test_case_4_outros_auto_promotion_with_google_books():
    """Case 4: Scanned PDF classified as 'outros' should be auto-promoted to 'livro' if Google Books confirms book."""
    enricher = MetadataEnricher()
    jev_result = JevValidationResult(
        classification="outros",
        classification_confidence=0.36,
        probabilities={},
        candidates=ExtractedCandidates(
            raw_title="Negritude sem identidade",
            raw_authors=["Érico ANDRADE"],
        ),
    )

    mock_gb = {
        "title": "Negritude sem identidade",
        "subtitle": "Sobre as narrativas singulares das pessoas negras",
        "authors": ["Érico Andrade"],
        "publisher": "n-1 edições",
        "year": 2026,
        "isbn": "9786561191005",
    }

    with patch.object(enricher, "fetch_google_books_by_title", return_value=mock_gb):
        meta = enricher.enrich(jev_result)

    assert meta.classification == "livro"
    assert meta.publisher == "n-1 edições"
    assert meta.identifiers.isbn == "9786561191005"
    fname = generate_standardized_filename(meta)
    assert fname.startswith("ANDRADE, Érico - Negritude sem identidade")


def test_case_5_compound_surname_and_no_cited_doi():
    """Case 5: Compound surname with 'e' formatted as 'FOLLADOR E AMBROSIO, Fabiana'."""
    formatted = format_single_author_abnt("Fabiana Follador e Ambrosio")
    assert formatted == "FOLLADOR E AMBROSIO, Fabiana"

    meta = PublicationMetadata(
        title="O estilo clínico ‘Ser e Fazer’ na investigação de benefícios clínicos de psicoterapias",
        authors=["Fabiana Follador e Ambrosio"],
        publisher="PUC Campinas",
        city="Campinas",
        year=2013,
        classification="tese",
        identifiers=Identifiers(),
    )
    abnt = ABNTFormatter.format(meta)
    assert "FOLLADOR E AMBROSIO, Fabiana" in abnt
    assert "Disponível em:" not in abnt  # No cited article DOI attached
