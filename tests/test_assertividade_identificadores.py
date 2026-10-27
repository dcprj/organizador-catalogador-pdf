"""Testes especializados de alta assertividade na extração de ISBN, ISSN e DOI,
rejeição de falsos títulos (epígrafes/disclaimers) e busca dirigida por nome de arquivo.
"""

from unittest.mock import patch, MagicMock
import pytest

from organizador_pdf.classifier_jev import (
    extrair_isbn_confiavel,
    extrair_issn_confiavel,
    extrair_doi_confiavel,
    is_invalid_or_disclaimer_title,
    is_prose_or_paragraph_title,
    extract_candidate_metadata,
)
from organizador_pdf.models import (
    ExtractedCandidates,
    JevValidationResult,
    PublicationMetadata,
    Identifiers,
    is_valid_isbn,
    is_valid_issn,
)
from organizador_pdf.metadata_api import MetadataEnricher


# ---------------------------------------------------------------------------
# 1. Testes de Extração Robusta de ISBN
# ---------------------------------------------------------------------------

def test_extrair_isbn_com_quebra_de_linha():
    texto = "Catalogação na fonte\nISBN:\n978-85-326-4531-9\n1. Psicologia."
    res = extrair_isbn_confiavel(texto)
    assert res is not None
    assert res[0] == "9788532645319"
    assert res[1] is True  # Checksum estrito válido


def test_extrair_isbn_com_rotulos_variados_e_parenteses():
    casos = [
        ("I.S.B.N. 85-326-0001-2", "8532600012"),
        ("I. S. B. N. 85-326-0001-2", "8532600012"),
        ("ISBN (impresso): 978-85-326-4531-9", "9788532645319"),
        ("ISBN (versão digital): 978-85-326-4532-6", "9788532645326"),
        ("ISBN-13: 978-85-326-4531-9", "9788532645319"),
        ("ISBN (brochura): 978–85–326–4531–9", "9788532645319"),  # En-dash unicode
    ]
    for texto, esperado in casos:
        res = extrair_isbn_confiavel(texto)
        assert res is not None, f"Falha ao extrair de: {texto}"
        assert res[0] == esperado


def test_extrair_isbn_standalone_sem_palavra_isbn():
    texto = "Publicado no Brasil. Dados: 978-85-326-4531-9. Depósito legal."
    res = extrair_isbn_confiavel(texto)
    assert res is not None
    assert res[0] == "9788532645319"


# ---------------------------------------------------------------------------
# 2. Testes de Extração Robusta de ISSN
# ---------------------------------------------------------------------------

def test_extrair_issn_com_prefixos_academicos():
    casos = [
        ("e-ISSN 1980-5470", "1980-5470"),
        ("p-ISSN 0102-7972", "0102-7972"),
        ("ISSN-L 1413-8271", "1413-8271"),
        ("ISSN (eletrônico): 1980-5470", "1980-5470"),
        ("ISSN (impresso): 0102-7972", "0102-7972"),
        ("ISSN: 1980 5470", "1980-5470"),  # Espaço no lugar de hífen
    ]
    for texto, esperado in casos:
        res = extrair_issn_confiavel(texto)
        assert res == esperado, f"Falha ao extrair ISSN de: {texto}"


# ---------------------------------------------------------------------------
# 3. Testes de Extração Robusta de DOI
# ---------------------------------------------------------------------------

def test_extrair_doi_em_cabecalho_com_volume_e_pagina():
    """Artigo legítimo traz v. e p. no cabeçalho e NÃO deve ter seu DOI descartado."""
    cabecalho = (
        "Revista Brasileira de Psicanálise, v. 50, n. 2, p. 115-130, 2016\n"
        "https://doi.org/10.1590/1982-02592016v50n2p115\n"
        "Artigo Original"
    )
    res = extrair_doi_confiavel(cabecalho)
    assert res == "10.1590/1982-02592016v50n2p115"


def test_extrair_doi_com_prefixo_doi_dois_pontos():
    texto = "Psicologia em Estudo, Maringá. DOI: 10.4025/psicolestud.v25i0.46321."
    res = extrair_doi_confiavel(texto)
    assert res == "10.4025/psicolestud.v25i0.46321"


# ---------------------------------------------------------------------------
# 4. Testes de Rejeição de Falsos Títulos (Epígrafes, Disclaimers, Redes)
# ---------------------------------------------------------------------------

def test_rejeitar_epigrafes_e_disclaimers_do_erros_log():
    falsos_titulos = [
        "Segundo a filosofia budista existem quatro formas de generosidade",
        "educação devem ser acessíveis e livres a toda e qualquer",
        "incentivou este projeto, colocando à disposição da comunidade gestáltica o m aterial de",
        "literare books international ltda, 2021.",
        "(CIP)",
        "CIP-BRASIL",
        "Titulooriginalem alemao",
        "Título original",
        "@psicologogabriel www.psicologogabriel.com",
        "Qual sua interpretação do desenho?",
        "do presente conteúdo",
        "livros de maior influência no progresso das ciências da psicologia analítica, nos",
    ]
    for ft in falsos_titulos:
        assert is_invalid_or_disclaimer_title(ft) is True, f"Deveria ter rejeitado: {ft}"


# ---------------------------------------------------------------------------
# 5. Busca Dirigida por Nome de Arquivo (Quando Identificador Ausente)
# ---------------------------------------------------------------------------

def test_fallback_pelo_nome_do_arquivo_com_autor_e_titulo():
    """Quando o PDF tem texto mas carece de ISBN, o nome do arquivo direciona a busca externa."""
    enricher = MetadataEnricher(online=True)
    candidates = ExtractedCandidates(
        raw_title="Segundo a filosofia budista existem quatro formas",  # Falso título
        raw_authors=[],
        fn_title="Memórias, Sonhos e Reflexões",
        fn_author="Carl Gustav Jung",
        sample_text="Texto do livro sem ficha catalográfica expressa.",
    )
    jev_result = JevValidationResult(
        classification="livro",
        classification_confidence=0.85,
        candidates=candidates,
    )

    mock_google_books = {
        "title": "Memórias, Sonhos e Reflexões",
        "subtitle": "Autobiografia",
        "authors": ["Carl Gustav Jung"],
        "publisher": "Nova Fronteira",
        "year": 2006,
        "isbn": "9788520932193",
    }

    with patch.object(enricher, "fetch_google_books_by_title", return_value=mock_google_books):
        meta = enricher.enrich(jev_result)

    assert meta.title == "Memórias, Sonhos e Reflexões"
    assert "Carl Gustav Jung" in meta.authors
    assert meta.publisher == "Nova Fronteira"
    assert meta.identifiers.isbn == "9788520932193"
