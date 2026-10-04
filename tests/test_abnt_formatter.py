"""Tests for ABNT NBR 6023:2018 formatter."""

import pytest
from src.models import PublicationMetadata, Identifiers
from src.abnt_formatter import (
    ABNTFormatter,
    format_single_author_abnt,
    format_authors_abnt,
)


def test_format_single_author():
    assert format_single_author_abnt("João da Silva") == "SILVA, João da"
    assert format_single_author_abnt("Carlos Alberto Souza Júnior") == "SOUZA JÚNIOR, Carlos Alberto"
    assert format_single_author_abnt("SANTOS, Maria") == "SANTOS, Maria"
    assert format_single_author_abnt("Aristóteles") == "ARISTÓTELES"


def test_format_authors_up_to_three():
    # 1 Author
    assert format_authors_abnt(["João Silva"]) == "SILVA, João"

    # 2 Authors
    res_2 = format_authors_abnt(["João Silva", "Maria Santos"])
    assert res_2 == "SILVA, João; SANTOS, Maria"

    # 3 Authors
    res_3 = format_authors_abnt(["João Silva", "Maria Santos", "Pedro Alvares"])
    assert res_3 == "SILVA, João; SANTOS, Maria; ALVARES, Pedro"


def test_format_authors_four_or_more_et_al():
    authors = ["João Silva", "Maria Santos", "Pedro Alvares", "Ana Clara"]
    res = format_authors_abnt(authors)
    assert res == "SILVA, João et al."


def test_abnt_book_reference():
    meta = PublicationMetadata(
        title="Inteligência Artificial",
        subtitle="Uma abordagem moderna",
        authors=["Stuart Russell", "Peter Norvig"],
        publisher="Campus",
        edition="3. ed.",
        city="Rio de Janeiro",
        year=2013,
        classification="livro",
        identifiers=Identifiers(isbn="9788535237016"),
    )
    ref = ABNTFormatter.format(meta)
    assert "RUSSELL, Stuart; NORVIG, Peter." in ref
    assert "**Inteligência Artificial**: Uma abordagem moderna." in ref
    assert "3. ed." in ref
    assert "Rio de Janeiro: Campus, 2013." in ref
    assert "ISBN 9788535237016." in ref


def test_abnt_scientific_article_reference():
    meta = PublicationMetadata(
        title="Attention Is All You Need",
        authors=[
            "Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit",
            "Llion Jones", "Aidan Gomez", "Lukasz Kaiser", "Illia Polosukhin"
        ],
        journal="Advances in Neural Information Processing Systems",
        volume="30",
        pages="5998-6008",
        year=2017,
        classification="artigo_cientifico",
        identifiers=Identifiers(doi="10.48550/arXiv.1706.03762"),
    )
    ref = ABNTFormatter.format(meta)
    assert "VASWANI, Ashish et al." in ref
    assert "Attention Is All You Need." in ref
    assert "**Advances in Neural Information Processing Systems**" in ref
    assert "v. 30, p. 5998-6008, 2017." in ref
    assert "Disponível em: <https://doi.org/10.48550/arXiv.1706.03762>." in ref


def test_abnt_courseware_reference():
    meta = PublicationMetadata(
        title="Estruturas de Dados e Algoritmos",
        subtitle="Apostila Teórica e Prática",
        authors=["Marcos Pontes"],
        publisher="Universidade de São Paulo",
        city="São Paulo",
        year=2024,
        classification="apostila",
    )
    ref = ABNTFormatter.format(meta)
    assert "PONTES, Marcos." in ref
    assert "**Estruturas de Dados e Algoritmos**: Apostila Teórica e Prática." in ref
    assert "São Paulo: Universidade de São Paulo, 2024." in ref
    assert "(Apostila)." in ref


def test_abnt_reference_no_author():
    meta = PublicationMetadata(
        title="Relatório Anual de Sustentabilidade",
        year=2022,
        publisher="Ministério do Meio Ambiente",
        city="Brasília",
        classification="livro",
    )
    ref = ABNTFormatter.format(meta)
    # First word in uppercase when no authors
    assert "**RELATÓRIO Anual de Sustentabilidade**." in ref
    assert "Brasília: Ministério do Meio Ambiente, 2022." in ref
