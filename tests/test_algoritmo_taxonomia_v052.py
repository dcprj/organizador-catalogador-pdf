"""Testes de homologação v0.5.2 - Onda 1: Frentes 1 a 5 da taxonomia e extração bibliográfica.

Cobre os 7 cenários definidos em .maestri/spec_v052_onda1.md:
1. Classificação temática em inglês e subáreas canônicas (Computação, IA, Retrocomputação, Análise do Comportamento).
2. Fallback de normalização JEV (Ciências Exatas e da Terra / Tecnologia -> Computação Geral; Crianças - Desenvolvimento).
3. Rejeição de colofões editoriais internacionais, marcas d'água DRM, CRB, créditos, louvores e palavras com espaçamento gráfico.
4. Divisão e higienização de coautores, titulações pós-nominais e preservação de sobrenomes compostos com 'e'.
5. Desambiguação de nomes de arquivo no formato Título - Autor, Título - Subtítulo - Autor e séries editoriais.
6. Reparo de mojibake CP437, entidades HTML, ligaduras tipográficas unicode e preservação de títulos hifenizados.
7. Cadenciamento anti-429 entre requisições externas e garantia de tipo_documental_confiante com identificadores validados.
"""

import os
import time
import pytest
from organizador_pdf.taxonomia import ALIASES_ASSUNTOS_INGLES, CATALOGO_TAXONOMICO
from organizador_pdf.classifier_jev import (
    is_invalid_or_disclaimer_title,
    normalizar_texto_bibliografico,
    normalizar_lista_autores,
    parse_filename_extended,
    extract_candidate_metadata,
)
from organizador_pdf.metadata_api import (
    MetadataEnricher,
    PublicationMetadata,
    JevValidationResult,
    _normalizar_area_jev_fallback,
    infer_thematic_area,
)
from organizador_pdf.abnt_formatter import format_authors_abnt


# ---------------------------------------------------------------------------
# GRUPO 1: Classificação temática em inglês e termos técnicos
# ---------------------------------------------------------------------------
def test_grupo_1_classificacao_ingles_e_termos_tecnicos():
    """Valida mapeamento temático de livros em inglês e termos técnicos de Computação e Psicologia."""
    # Data Science / Machine Learning
    area, subarea = infer_thematic_area(
        title="Doing Data Science: Straight Talk from the Frontline",
        sample_text="This book covers data science, statistical modeling, machine learning algorithms and algorithms in python.",
    )
    assert area == "Computação"
    assert subarea == "Inteligência Artificial e Ciência de Dados"

    # Big Data / Hadoop / Spark
    area, subarea = infer_thematic_area(
        title="Hadoop: The Definitive Guide",
        sample_text="Storage and analysis at internet scale using MapReduce, Apache Spark, HDFS and big data clusters.",
    )
    assert area == "Computação"
    assert subarea == "Inteligência Artificial e Ciência de Dados"

    # Programação R / RStudio
    area, subarea = infer_thematic_area(
        title="R Programming for Data Science",
        sample_text="An introduction to programming in R, CRAN packages, RStudio and data analysis pipelines.",
    )
    assert area == "Computação"
    assert subarea == "Inteligência Artificial e Ciência de Dados"

    # Python / Automação
    area, subarea = infer_thematic_area(
        title="Automate the Boring Stuff with Python",
        sample_text="Practical programming for total beginners using python scripts, regex and automation.",
    )
    assert area == "Computação"
    assert subarea == "Algoritmos e Programação"

    # Engenharia de Software / Software Craftsmanship
    area, subarea = infer_thematic_area(
        title="The Software Craftsman: Professionalism, Pragmatism, Pride",
        sample_text="Clean code, agile software development, testing, refactoring and craftsmanship.",
    )
    assert area == "Computação"
    assert subarea == "Engenharia de Software"

    # Retrocomputação e Revistas Históricas
    area, subarea = infer_thematic_area(
        title="Revista Micro Sistemas - Edição Histórica",
        sample_text="Artigos sobre MSX, Sinclair, TK-85, computadores de 8 bits, retrocomputação e hardware dos anos 80.",
    )
    assert area == "Computação"
    assert subarea == "Sistemas Embarcados e Retrocomputação"

    # Análise do Comportamento / Skinneriano / Comportamento governado por regras
    area, subarea = infer_thematic_area(
        title="Comportamento Governado por Regras e Contingências",
        sample_text="Análise experimental do comportamento, condicionamento operante, skinneriano e contingências de reforço.",
    )
    assert area == "Psicologia"
    assert subarea == "Análise do Comportamento"


# ---------------------------------------------------------------------------
# GRUPO 2: Normalização de fallback JEV
# ---------------------------------------------------------------------------
def test_grupo_2_normalizacao_fallback_jev():
    """Valida mapeamentos canônicos para categorias amplas do JEV."""
    # Ciências Exatas e da Terra / Tecnologia -> Computação / Computação Geral
    area_c, subarea_c = _normalizar_area_jev_fallback("Ciências Exatas e da Terra", "Tecnologia")
    assert area_c == "Computação"
    assert subarea_c == "Computação Geral"

    # Exatas / Informática
    area_inf, subarea_inf = _normalizar_area_jev_fallback("Ciências Exatas", "Informática")
    assert area_inf == "Computação"
    assert subarea_inf == "Computação Geral"

    # Crianças - Desenvolvimento -> Psicologia / Psicologia do Desenvolvimento
    area_p, subarea_p = _normalizar_area_jev_fallback("Psicologia", "Crianças - Desenvolvimento")
    assert area_p == "Psicologia"
    assert subarea_p == "Psicologia do Desenvolvimento"

    # Ciências Sociais Aplicadas / Direito -> Direito / Direito Geral
    area_dir, subarea_dir = _normalizar_area_jev_fallback("Ciências Sociais Aplicadas", "Direito")
    assert area_dir == "Direito"
    assert subarea_dir == "Direito Geral"


# ---------------------------------------------------------------------------
# GRUPO 3: Rejeição de colofões, marcas d'água DRM, CRB e louvores
# ---------------------------------------------------------------------------
def test_grupo_3_rejeicao_colofoes_drm_crb_e_louvores():
    """Verifica que cabeçalhos espúrios são prontamente rejeitados como título da obra."""
    poluicoes = [
        "Beijing • Cambridge • Farnham • Köln • Sebastopol • Tokyo",
        "Boston • Columbus • Indianapolis • New York • San Francisco",
        "Licenciado para - Fulano de Tal - 12345678900",
        "Licenciado para João Silva - 000.111.222-33 - Protegido por Direitos Autorais",
        "Angélica Ilacqua CRB-8 / 7057",
        "CRB-10 / 1234",
        "Bibliotecária responsável: Maria Santos CRB 9/4321",
        "Praise for Doing Data Science",
        "Praise for Clean Code",
        "Table of Contents",
        "Credits and Acknowledgments",
        "Version 1.0.4",
        "Versão 2.1",
        "O F E R T A",
        "P R O M O Ç Ã O",
    ]
    for p in poluicoes:
        assert is_invalid_or_disclaimer_title(p) is True, f"Deveria ter rejeitado: {p}"

    # Valida que títulos reais com espaçamento normal NÃO são rejeitados
    assert is_invalid_or_disclaimer_title("Doing Data Science") is False
    assert is_invalid_or_disclaimer_title("The Software Craftsman") is False
    assert is_invalid_or_disclaimer_title("Comportamento Humano e Liberdade") is False


# ---------------------------------------------------------------------------
# GRUPO 4: Divisão e higienização de coautores e titulações pós-nominais
# ---------------------------------------------------------------------------
def test_grupo_4_divisao_e_limpeza_coautores_e_titulacoes():
    """Testa divisão de múltiplos coautores (&, and), descarte de graus pós-nominais e preservação de sobrenome com 'e'."""
    # Descarte de titulações pós-nominais acadêmicas e médicas
    res_phd = normalizar_lista_autores(["Jane Doe, Ph.D.", "John Smith, MD"])
    assert res_phd == ["Jane Doe", "John Smith"]

    res_dphil = normalizar_lista_autores(["Simon Baron-Cohen, FBA FMedSci PhD", "Alice Walker, MB BS MA DPhil"])
    assert res_dphil == ["Simon Baron-Cohen", "Alice Walker"]

    # Divisão de coautoria com '&'
    res_amp = normalizar_lista_autores(["Daniel Z. Lieberman & Michael E. Long"])
    assert res_amp == ["Daniel Z. Lieberman", "Michael E. Long"]

    # Divisão de coautoria mista com vírgula e 'and'
    res_and = normalizar_lista_autores(["Ian Goodfellow, Yoshua Bengio and Aaron Courville"])
    assert res_and == ["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"]

    # Formatação ABNT para coautoria mista
    abnt_gf = format_authors_abnt(res_and)
    assert abnt_gf == "GOODFELLOW, Ian; BENGIO, Yoshua; COURVILLE, Aaron"

    # PRESERVAÇÃO: sobrenome composto brasileiro ligado por 'e' NÃO deve ser dividido!
    res_comp = normalizar_lista_autores(["Fabiana Follador e Ambrosio"])
    assert res_comp == ["Fabiana Follador e Ambrosio"]
    assert format_authors_abnt(res_comp) == "FOLLADOR E AMBROSIO, Fabiana"


# ---------------------------------------------------------------------------
# GRUPO 5: Desambiguação de nomes de arquivo (Título - Autor / Título - Subtítulo - Autor)
# ---------------------------------------------------------------------------
def test_grupo_5_desambiguacao_nomes_de_arquivo():
    """Testa heurísticas de separação autor/título em arquivos com calibres variados."""
    # Formato: Título - Autor.pdf
    meta1 = parse_filename_extended("/tmp/Big Data - Bernard Marr.pdf")
    assert meta1["title"] == "Big Data"
    assert meta1["author"] == "Bernard Marr"

    # Formato: Título - Coautores (com vírgula e and)
    meta2 = parse_filename_extended("/tmp/Deep Learning - Ian Goodfellow, Yoshua Bengio and Aaron Courville.pdf")
    assert meta2["title"] == "Deep Learning"
    assert meta2["author"] == "Ian Goodfellow, Yoshua Bengio and Aaron Courville"

    # Formato: Título - Subtítulo - Autor.pdf (3 partes)
    meta3 = parse_filename_extended("/tmp/Think Bayes - Bayesian Statistics Made Simple - Allen B. Downey.pdf")
    assert meta3["title"] == "Think Bayes - Bayesian Statistics Made Simple"
    assert meta3["author"] == "Allen B. Downey"

    # Formato com prefixo editorial em parênteses: (Series) Autor - Título.pdf
    meta4 = parse_filename_extended("/tmp/(Robert C. Martin Series) Sandro Mancuso - The software craftsman.pdf")
    assert meta4["author"] == "Sandro Mancuso"
    assert meta4["title"] == "The software craftsman"

    # Formato: Autor - Título.pdf
    meta5 = parse_filename_extended("/tmp/Cida Bento - O Pacto da Branquitude.pdf")
    assert meta5["author"] == "Cida Bento"
    assert meta5["title"] == "O Pacto da Branquitude"


# ---------------------------------------------------------------------------
# GRUPO 6: Reparo de Mojibake CP437, HTML unescape, ligaduras e títulos hifenizados
# ---------------------------------------------------------------------------
def test_grupo_6_mojibake_html_ligaduras_e_titulos_hifenizados():
    """Valida limpeza e reparo ortográfico de textos extraídos de PDFs com encoding legado ou artefatos."""
    # Mojibake CP437 / DOS-850 corriqueiro em arquivos OCR legados
    raw_mojibake = "O Sistema ╔tico Skinneriano e ConseqⁿΩncias da Aτπo"
    repaired = normalizar_texto_bibliografico(raw_mojibake)
    assert "Ético" in repaired or "Ético" in repaired.title()
    assert "Conseqüências" in repaired or "Consequências" in repaired or "Ação" in repaired

    # Entidades HTML unescaped
    raw_html = "Data Science &amp; Big Data Analysis &lt;Principles&gt;"
    assert normalizar_texto_bibliografico(raw_html) == "Data Science & Big Data Analysis <Principles>"

    # Ligaduras tipográficas (ex: \ufb01 -> fi, \ufb02 -> fl)
    raw_ligature = "Artiﬁcial Intelligence & Inﬂuence"
    assert normalizar_texto_bibliografico(raw_ligature) == "Artificial Intelligence & Influence"

    # Títulos hifenizados não devem ser barrados como palavras únicas na avaliação de revisão
    enricher = MetadataEnricher()
    meta = PublicationMetadata(
        title="open-source-evolucao-e-tendencias",
        classification="livro",
        confidence=0.85,
        area="Computação",
        subarea="Linguagens e Programação",
        authors=["Linus Torvalds"],
        year=2021,
    )
    enricher._evaluate_needs_review(meta)
    # Não deve ter recebido aviso de 'Documento com título de palavra única'
    single_word_warnings = [r for r in meta.review_reasons if "palavra única" in r.lower()]
    assert len(single_word_warnings) == 0


# ---------------------------------------------------------------------------
# GRUPO 7: Cadenciamento anti-429 e tipo_documental_confiante com ID validado
# ---------------------------------------------------------------------------
def test_grupo_7_cadenciamento_e_tipo_documental_confiante():
    """Testa cadência thread-safe entre provedores e isenção de quarentena para IDs confirmados."""
    enricher = MetadataEnricher()

    # 1. Cadenciamento de requisições: valida que método respeita intervalo configurado
    t0 = time.perf_counter()
    enricher._aguardar_cadencia_provedor("google_books", min_interval=0.08)
    enricher._aguardar_cadencia_provedor("google_books", min_interval=0.08)
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.07, f"Cadenciamento deveria ter pausado ao menos 0.07s, levou {elapsed:.3f}s"

    # 2. Obra com ISBN validado e classificação bibliográfica mantém tipo_documental_confiante=True
    meta_valid = PublicationMetadata(
        title="A Vida Psíquica do Poder: Teorias da Sujeição",
        classification="livro",
        confidence=0.80,
        area="Filosofia",
        subarea="Filosofia Política",
        authors=["Judith Butler"],
        year=2017,
        isbn_verified=True,
    )
    meta_valid.identifiers.isbn = "9788574785412"

    jev_res = JevValidationResult(
        is_valid=True,
        classification="livro",
        classification_confidence=0.45,  # Confiança local baixa (< 0.50), porém ISBN verificado
    )

    enricher._evaluate_needs_review(meta_valid, jev_result=jev_res, sources_consulted=["Google Books"])
    # Com has_verified_id=True, baixa confiança de JEV NÃO deve rebaixar nem reter em revisão manual
    assert meta_valid.needs_review is False
    assert not any("confiança de classificação baixa" in r.lower() for r in meta_valid.review_reasons)
