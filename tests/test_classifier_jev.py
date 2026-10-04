"""Tests for Jev (System One) Classifier and Candidate Metadata Extraction."""

from unittest.mock import MagicMock, patch
import pytest
from src.classifier_jev import (
    extract_native_sample_text,
    extract_candidate_metadata,
    JevClassifier,
)
from src.models import ExtractedCandidates


def test_extract_native_sample_text(sample_pdf_generator):
    pages = [f"Page content {i} with some academic text." for i in range(1, 8)]
    pdf_path = sample_pdf_generator("multipage.pdf", "MultiPage Test", pages)

    combined_text, page_list = extract_native_sample_text(str(pdf_path), max_pages=5)
    # Strictly max 5 pages
    assert len(page_list) == 5
    assert "Page content 1" in combined_text
    assert "Page content 5" in combined_text
    assert "Page content 6" not in combined_text


def test_extract_candidate_metadata_regex():
    sample_text = (
        "Deep Learning for Vision\n"
        "Por: Yann LeCun, Yoshua Bengio\n"
        "Editora: MIT Press\n"
        "doi: 10.1145/3065386\n"
        "ISBN: 978-0-262-03561-3\n"
        "ISSN: 0001-0782\n"
    )
    candidates = extract_candidate_metadata(sample_text)
    assert candidates.doi == "10.1145/3065386"
    assert candidates.isbn == "9780262035613"
    assert candidates.issn == "0001-0782"
    assert candidates.raw_title == "Deep Learning for Vision"
    assert "Yann LeCun" in candidates.raw_authors
    assert candidates.raw_publisher == "MIT Press"


def test_jev_classifier_with_mocked_sdk(sample_pdf_generator):
    sample_pages = [
        "Estudo de Caso em Aprendizado por Reforço\n"
        "Autores: Carlos Silva, Roberto Rocha\n"
        "doi: 10.1016/j.artint.2023.103982\n"
        "Abstract: We propose a novel architecture..."
    ]
    pdf_path = sample_pdf_generator("artigo.pdf", "Artigo", sample_pages)

    # Mock TypeSafeClient and system_one response
    mock_choice = MagicMock()
    mock_choice.choice = "artigo_cientifico"
    mock_choice.confidence = 0.98

    mock_noul_title = MagicMock()
    mock_noul_title.noul = 0.99
    mock_noul_authors = MagicMock()
    mock_noul_authors.noul = 0.97
    mock_noul_pub = MagicMock()
    mock_noul_pub.noul = 0.90
    mock_noul_isbn = MagicMock()
    mock_noul_isbn.noul = 0.10
    mock_noul_doi = MagicMock()
    mock_noul_doi.noul = 0.99
    mock_noul_issn = MagicMock()
    mock_noul_issn.noul = 0.15

    mock_response = MagicMock()
    mock_response.choices = {"classification": mock_choice}
    mock_response.nouls = {
        "has_valid_title": mock_noul_title,
        "has_valid_authors": mock_noul_authors,
        "has_valid_publisher": mock_noul_pub,
        "has_valid_isbn": mock_noul_isbn,
        "has_valid_doi": mock_noul_doi,
        "has_valid_issn": mock_noul_issn,
    }

    mock_client_instance = MagicMock()
    mock_client_instance.system_one.return_value = mock_response
    mock_client_instance.__enter__.return_value = mock_client_instance

    mock_sdk_mod = MagicMock(TypeSafeClient=MagicMock(return_value=mock_client_instance))
    with patch.dict("sys.modules", {"typesafe_sdk": mock_sdk_mod}):
        classifier = JevClassifier(api_key="test_api_key")
        result = classifier.classify_and_validate(str(pdf_path))

    assert result.classification in ("artigo", "artigo_cientifico")
    assert result.classification_confidence == 0.98
    assert result.probabilities["doi"] == 0.99
    assert result.probabilities["title"] == 0.99
    assert result.candidates.doi == "10.1016/j.artint.2023.103982"


def test_extract_native_sample_text_head_and_tail(sample_pdf_generator):
    pages = [f"Page {i} content" for i in range(1, 26)]  # 25 pages
    pdf_path = sample_pdf_generator("book_sample.pdf", "Head Tail Test", pages)

    combined_text, page_list = extract_native_sample_text(str(pdf_path), head_pages=10, tail_pages=10)
    assert len(page_list) == 20
    # Head pages present
    assert "Page 1 content" in combined_text
    assert "Page 10 content" in combined_text
    # Middle pages omitted
    assert "Page 11 content" not in combined_text
    assert "Page 15 content" not in combined_text
    # Tail pages present
    assert "Page 16 content" in combined_text
    assert "Page 25 content" in combined_text


def test_jev_calibrated_fallback_thesis(sample_pdf_generator, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    sample_pages = [
        "UNIVERSIDADE DE SÃO PAULO\n"
        "Programa de Pós-Graduação em Ciência da Computação\n"
        "Tese de Doutorado em Inteligência Artificial\n"
        "Tese apresentada para obtenção do título de Doutor em Ciências\n"
        "Autor: Carlos Drummond\n"
        "Orientador: Prof. Dr. Silva"
    ]
    pdf_path = sample_pdf_generator("tese.pdf", "Tese Teste", sample_pages)

    classifier = JevClassifier(api_key=None)
    result = classifier.classify_and_validate(str(pdf_path))

    assert result.classification == "tese"


def test_jev_calibrated_fallback_book(sample_pdf_generator, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    sample_pages = [
        "Fundamentos de Física Quântica\n"
        "ISBN 978-85-216-1234-5\n"
        "Editora LTC\n"
        "Ficha catalográfica elaborada pela biblioteca..."
    ]
    pdf_path = sample_pdf_generator("livro.pdf", "Livro Teste", sample_pages)

    # Without api_key, runs calibrated fallback
    classifier = JevClassifier(api_key=None)
    result = classifier.classify_and_validate(str(pdf_path))

    assert result.classification == "livro"
    assert result.probabilities["isbn"] >= 0.95
    assert result.candidates.isbn == "9788521612345"


def test_jev_calibrated_fallback_courseware(sample_pdf_generator, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    sample_pages = [
        "Apostila de Introdução ao Python\n"
        "Material didático para curso de computação\n"
        "Notas de aula e exercícios propostos..."
    ]
    pdf_path = sample_pdf_generator("apostila.pdf", "Apostila Teste", sample_pages)

    classifier = JevClassifier(api_key=None)
    result = classifier.classify_and_validate(str(pdf_path))

    assert result.classification == "apostila"


def test_parse_page_cip_single_author():
    from src.classifier_jev import parse_page_cip

    sample_page = (
        "CIP-BRASIL. CATALOGAÇÃO NA PUBLICAÇÃO\n"
        "SINDICATO NACIONAL DOS EDITORES DE LIVROS, RJ\n"
        "Birman, Joel, 1946-\n"
        "As pulsões e seus destinos [recurso eletrônico]: do corporal ao psíquico / Joel Birman. - 1. ed. - Rio de Janeiro: Best Seller, 2016.\n"
        "ISBN 978-85-200-1234-5\n"
    )
    res = parse_page_cip(sample_page)
    assert res is not None
    assert res["title"] == "As pulsões e seus destinos"
    assert res["subtitle"] == "do corporal ao psíquico"
    assert res["authors"] == ["Joel Birman"]
    assert res["edition"] == "1. ed."
    assert res["city"] == "Rio de Janeiro"
    assert res["publisher"] == "Best Seller"
    assert res["year"] == 2016
    assert res["isbn"] == "9788520012345"


def test_parse_page_cip_edited_collection():
    from src.classifier_jev import parse_page_cip

    sample_page = (
        "N494\n"
        "Neuropsicologia [recurso eletrônico] : teoria e prática / \n"
        "Organizadores, Daniel Fuentes ... [et al.]. – 2. ed. – Dados eletrônicos. – Porto Alegre : Artmed, 2014.\n"
        "ISBN 978-85-8271-056-2\n"
        "CDU 616.8:159.9\n"
        "Catalogação na publicação: Ana Paula M. Magnus – CRB-10/2052\n"
    )
    res = parse_page_cip(sample_page)
    assert res is not None
    assert res["title"] == "Neuropsicologia"
    assert res["subtitle"] == "teoria e prática"
    assert "Daniel Fuentes" in res["authors"]
    assert "et al." in res["authors"]
    assert res["edition"] == "2. ed."
    assert res["city"] == "Porto Alegre"
    assert res["publisher"] == "Artmed"
    assert res["year"] == 2014


def test_parse_page_cip_multi_author_courseware():
    from src.classifier_jev import parse_page_cip

    sample_page = (
        "Ficha catalográfica elaborada pela Biblioteca Universitária da Unisul\n"
        "Copyright © UnisulVirtual 2011\n"
        "Nenhuma parte desta publicação pode ser reproduzida...\n"
        "302 T83\n"
        "Tumolo, Ligia Maria Soufen\n"
        "Psicologia social : livro didático / Ligia Maria Soufen Tumolo, Carolina Hoeller da Silva Boeing ; design instrucional Leandro Kingeski Pacheco. – 4. ed. – Palhoça : UnisulVirtual, 2011.\n"
    )
    res = parse_page_cip(sample_page)
    assert res is not None
    assert res["title"] == "Psicologia social"
    assert res["subtitle"] == "livro didático"
    assert "Ligia Maria Soufen Tumolo" in res["authors"]
    assert "Carolina Hoeller da Silva Boeing" in res["authors"]
    assert res["edition"] == "4. ed."
    assert res["city"] == "Palhoça"
    assert res["publisher"] == "UnisulVirtual"
    assert res["year"] == 2011
