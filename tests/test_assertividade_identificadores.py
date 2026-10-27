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


# ---------------------------------------------------------------------------
# 6. Testes das 4 Melhorias: Reconciliação Canônica, Disclaimers, Autores e Fascículos
# ---------------------------------------------------------------------------

def test_melhoria_1_falso_conflito_isbn_canonico_confirmado():
    """Caso 'A Síndrome do Impostor': divergência reconciliada pela base oficial canônica não gera quarentena."""
    enricher = MetadataEnricher(online=True)
    metadata = PublicationMetadata(
        title="A Síndrome do Impostor",
        authors=["Kevin Cokley"],
        publisher="Editora Planeta",
        year=2020,
        city="São Paulo",
        classification="livro",
        confidence=0.95,
        confidence_classification=0.95,
        confidence_metadata=0.95,
        identifiers=Identifiers(isbn="9788542217032"),
        isbn_verified=True,
    )
    # Simula divergência reconciliada registrada como âncora canônica
    metadata.conflitos_registrados.append(
        "Divergência entre candidato local ('Apostila de Treinamento') e registro canônico confirmado no Brasil API / CBL "
        "('A Síndrome do Impostor') para ISBN confirmado. Registro canônico adotado como âncora da obra."
    )

    jev_result = JevValidationResult(
        classification="livro",
        classification_confidence=0.95,
        candidates=ExtractedCandidates(raw_title="A Síndrome do Impostor", isbn="9788542217032"),
    )

    enricher._evaluate_needs_review(
        metadata,
        jev_result=jev_result,
        sources_consulted=["Brasil API / CBL (ISBN)"],
    )

    assert metadata.needs_review is False
    assert metadata.revisao_manual_necessaria is False
    assert metadata.identidade_confirmada is True
    assert metadata.identidade_corroborada_fonte is True
    assert not any("Conflito bibliográfico irreconciliável" in r for r in metadata.review_reasons)


def test_melhoria_2_disclaimers_ebooks_e_digitalizacao_coletiva():
    """Caso 'Carl Rogers': detecção de disclaimers de crowdfunding/digitalização com ligaduras unicode e fallback para folha de rosto/fn_title."""
    disclaimers = [
        "Você pode ajudar contribuindo de várias maneiras",
        "Ajude o nosso crowdfunding",
        "Dados de Odinright elivros",
        "elivros.love livros grátis",
        "converted by epubtopdf",
        "digitalizado por le livros",
        "distribuição gratuita sem fins comerciais",
        "apenas para uso acadêmico",
        "Ou ainda podendo ajudar \ufb01nanceiramente a pagar custo de servidor",
        "podendo ajudar a pagar custo de hospedagem",
        "manutenção da biblioteca e do site",
        "servidores e obras que compramos para postar, faça uma",
        "faça uma doação via chave pix",
        "a presente obra é disponibilizada com fim exclusivo de compra futura",
    ]
    for d in disclaimers:
        assert is_invalid_or_disclaimer_title(d) is True, f"Deveria ter rejeitado: {d}"

    # 1. Quando há página de disclaimer de Odinright/eLivros seguida de folha de rosto canônica (ex: página 4)
    sample_com_folha_de_rosto = (
        "DADOS DE ODINRIGHT\n"
        "eLivros\n"
        "servidores e obras que compramos para postar, faça uma\n\n"
        "De PESSOA para PESSOA\n"
        "Carl R. Rogers"
    )
    cands_folha = extract_candidate_metadata(
        sample_text=sample_com_folha_de_rosto,
        pdf_path="/caminho/ficticio/De-Pessoa-para-Pessoa-o-Problema-de-Ser-Humano-Carl-Rogers.pdf",
        reabrir_pdf=False,
    )
    assert cands_folha.raw_title == "De PESSOA para PESSOA"

    # 2. Quando o PDF contém apenas o bloco de disclaimer, faz fallback seguro para fn_title
    sample_text_disclaimer_puro = (
        "DADOS DE ODINRIGHT\n"
        "ePubtoPDF\n"
        "Ou ainda podendo ajudar \ufb01nanceiramente a pagar custo de servidores e obras que compramos para postar, faça uma doação\n"
        "distribuição gratuita sem fins comerciais"
    )
    cands_fn = extract_candidate_metadata(
        sample_text=sample_text_disclaimer_puro,
        pdf_path="/caminho/ficticio/De-Pessoa-para-Pessoa-o-Problema-de-Ser-Humano-Carl-Rogers.pdf",
        reabrir_pdf=False,
    )
    assert cands_fn.raw_title == "De-Pessoa-para-Pessoa-o-Problema-de-Ser-Humano-Carl-Rogers"
    assert cands_fn.title_source == "nome_arquivo"


def test_melhoria_3_autores_falsos_propriedades_pdf_vs_nome_arquivo():
    """Casos 'Morana' e 'Couto': rejeição de operadores técnicos, higienização de 'et al' e formatação ABNT limpa."""
    from organizador_pdf.abnt_formatter import format_authors_abnt

    # 1. Operador técnico descartado e sufixo 'et al' limpo do autor base
    cands_op = extract_candidate_metadata(
        sample_text="Artigo de Psiquiatria Forense\nTexto sobre perícias e laudos.",
        pdf_path="/caminho/Morana et al - Perícia Psiquiátrica Forense.pdf",
        reabrir_pdf=False,
        metadados_embutidos={"author": "daidigital", "title": "Perícia Psiquiátrica Forense"},
    )
    assert cands_op.raw_authors == ["Morana"]
    assert cands_op.author_source == "nome_arquivo"
    # Garante que format_authors_abnt não gere "AL, Morana et et al."
    abnt_formatted = format_authors_abnt(cands_op.raw_authors)
    assert "AL, Morana" not in abnt_formatted
    assert abnt_formatted == "MORANA"

    # 2. Palavra única nas propriedades ('Couto') vs fn_author ('Couto et al')
    cands_single = extract_candidate_metadata(
        sample_text="Artigo sobre Direitos Humanos e Criminologia.",
        pdf_path="/caminho/Couto et al - Criminologia e Justiça.pdf",
        reabrir_pdf=False,
        metadados_embutidos={"author": "Couto", "title": "Criminologia e Justiça"},
    )
    assert cands_single.raw_authors == ["Couto"]
    assert cands_single.author_source == "nome_arquivo"
    assert format_authors_abnt(cands_single.raw_authors) == "COUTO"

    # 3. Se entrada bruta ainda contiver 'et al', formatador ABNT higieniza sem tratar 'al' como sobrenome
    assert format_authors_abnt(["Morana et al"]) == "MORANA et al."
    assert format_authors_abnt(["Couto et al."]) == "COUTO et al."


def test_melhoria_4_cabecalho_fasciculo_revista_como_titulo():
    """Caso 'Gonçalves et al': rejeição de fascículo e mês/ano (ex: 'NÚMERO 6', 'DEZEMBRO 1999') com extração do título real."""
    fasciculos_e_datas = [
        "Volume 10 - Número 2",
        "Vol. 4 / Fascículo 1",
        "Ano 2021 - Issue 3",
        "volume 12",
        "NÚMERO 6",
        "nº 4",
        "fascículo 2",
        "DEZEMBRO 1999",
        "dezembro 1999",
        "janeiro 2020",
        "MARÇO 2015",
        "October 2018",
    ]
    for fd in fasciculos_e_datas:
        assert is_invalid_or_disclaimer_title(fd) is True, f"Deveria ter rejeitado: {fd}"

    # Simula documento com "NÚMERO 6" na primeira linha e "DEZEMBRO 1999" na segunda linha
    sample_text = (
        "NÚMERO 6\n"
        "DEZEMBRO 1999\n"
        "Avaliacao de Servico de Atencao a Criancas Vitimas de Violencia Domestica\n"
        "Texto introdutório sobre o serviço de proteção a menores."
    )
    cands = extract_candidate_metadata(
        sample_text=sample_text,
        pdf_path="/caminho/Gonçalves et al - Avaliacao de Servico de Atencao a Criancas Vitimas de Violencia Domestica.pdf",
        reabrir_pdf=False,
    )

    assert cands.raw_title == "Avaliacao de Servico de Atencao a Criancas Vitimas de Violencia Domestica"
    assert cands.raw_authors == ["Gonçalves"]
    assert cands.author_source == "nome_arquivo"


# ---------------------------------------------------------------------------
# 7. Testes dos 3 Ajustes Cirúrgicos: Underscores, Cabeçalho CIP e Citação de Periódicos
# ---------------------------------------------------------------------------

def test_ajuste_1_underscores_nome_arquivo():
    """Underscores no nome do arquivo são substituídos por espaços evitando palavras aglutinadas."""
    from organizador_pdf.classifier_jev import parse_filename_extended, sanitize_title_candidate

    res1 = parse_filename_extended("/caminho/Casos_Clinicos_em_Saude_Mental.pdf")
    assert res1["title"] == "Casos Clinicos em Saude Mental"

    res2 = parse_filename_extended("/caminho/Analise_do_comportamento - Skinner.pdf")
    assert res2["title"] == "Analise do comportamento"
    assert res2["author"] == "Skinner"

    res3 = parse_filename_extended("/caminho/Skinner - Analise_do_comportamento.pdf")
    assert res3["title"] == "Analise do comportamento"
    assert res3["author"] == "Skinner"

    clean_t = sanitize_title_candidate("Casos_Clinicos_em_Saude_Mental")
    assert clean_t == "Casos Clinicos em Saude Mental"


def test_ajuste_2_cabecalho_ficha_cip_como_titulo():
    """Expressões institucionais da ficha CIP são rejeitadas como título com fallback para fn_title."""
    from organizador_pdf.classifier_jev import is_invalid_or_disclaimer_title, is_generic_or_structural_title

    cip_headers = [
        "Câmara Brasileira do Livro, SP, Brasil",
        "camara brasileira do livro",
        "Sindicato Nacional dos Editores de Livros, RJ",
        "Dados Internacionais de Catalogação na Publicação (CIP)",
        "Catalogação na fonte",
        "Bibliotecária responsável: Maria Silva CRB-8/1234",
        "Bibliotecario responsavel",
    ]
    for ch in cip_headers:
        assert is_generic_or_structural_title(ch) is True, f"Deveria ser estrutural: {ch}"
        assert is_invalid_or_disclaimer_title(ch) is True, f"Deveria ter rejeitado: {ch}"

    # Fallback para fn_title limpo (Caso 'Dupla Excepcionalidade')
    sample_text = (
        "Dados Internacionais de Catalogação na Publicação (CIP)\n"
        "Câmara Brasileira do Livro, SP, Brasil\n"
        "Texto introdutório sobre altas habilidades e desenvolvimento."
    )
    cands = extract_candidate_metadata(
        sample_text=sample_text,
        pdf_path="/caminho/Dupla_Excepcionalidade_Altas_Habilidades.pdf",
        reabrir_pdf=False,
    )
    assert cands.raw_title == "Dupla Excepcionalidade Altas Habilidades"
    assert cands.title_source == "nome_arquivo"


def test_ajuste_3_cabecalho_citacao_periodicos_como_titulo():
    """Cabeçalhos e citações estruturais de periódicos são rejeitados como título com fallback para o artigo."""
    from organizador_pdf.classifier_jev import is_invalid_or_disclaimer_title, is_generic_or_structural_title

    journal_headers = [
        "Psicologia em Estudo, Maringá, v. 14, n. 2, p. 301-310, abr./jun. 2009",
        "psicologia em estudo",
        "Psicologia Ciência e Profissão, 2011, 31 (2), 240-255",
        "Arquivos de Neuro-Psiquiatria 2005; 63(3-A): 613-619",
        "Arq Neuro-Psiquiatr 2005; 63: 613-619",
        "v. 10, n. 1, p. 20-30",
        "Vol. 15, No. 2, pp. 100-115",
    ]
    for jh in journal_headers:
        assert is_generic_or_structural_title(jh) is True, f"Deveria ser estrutural: {jh}"
        assert is_invalid_or_disclaimer_title(jh) is True, f"Deveria ter rejeitado: {jh}"

    # Fallback para fn_title do artigo (Casos Fontana, Monteiro, Biazus, Ferreira Neto)
    sample_text = (
        "Psicologia em Estudo, Maringá, v. 14, n. 2, p. 301-310, 2009\n"
        "Texto introdutório sobre o estudo."
    )
    cands = extract_candidate_metadata(
        sample_text=sample_text,
        pdf_path="/caminho/Fontana - O Desenvolvimento Moral e a Afetividade.pdf",
        reabrir_pdf=False,
    )
    assert cands.raw_title == "O Desenvolvimento Moral e a Afetividade"
    assert cands.raw_authors == ["Fontana"]
    assert cands.title_source == "nome_arquivo"
