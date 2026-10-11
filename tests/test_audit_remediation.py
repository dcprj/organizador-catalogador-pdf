"""Testes de regressão para as 6 correções do Parecer Técnico (teste_1).

1. Identificação de ISBN e ficha CIP (Gestalt-terapia, traços, espaços).
2. Proteção de dados clínicos/psicológicos sensíveis (SRS-2, ASRS) e privacidade.
3. Gatekeeper temático (bloqueio de auto-organização com Outros/Geral/0.0).
4. Sincronização 1:1:1 de auditoria, metadados e prevenção de auditorias órfãs.
5. Registro completo de consultas externas e filtro de autores institucionais.
6. Remoção de campos duplicados de identificadores no Markdown.
"""

from pathlib import Path
import json
import time
import pytest

from organizador_pdf.models import (
    Metadados,
    PublicationMetadata,
    Identificadores,
    JevValidationResult,
    ExtractedCandidates,
    TipoPublicacao,
    RegistroRastreabilidade,
    ConsultaFonteExterna,
    StatusConsultaProvedor,
)
from organizador_pdf.classifier_jev import (
    parse_page_cip,
    JevClassifier,
    is_invalid_or_disclaimer_title,
)
from organizador_pdf.metadata_api import (
    MetadataEnricher,
    infer_thematic_area,
    confirmar_mesma_obra,
)
from organizador_pdf.converter import MarkdownConverter
from organizador_pdf.organizer import organizar, gerar_markdown
from organizador_pdf.pipeline import Pipeline, Config, OpcoesDoPipeline
from organizador_pdf.abnt_formatter import ABNTFormatter


# ---------------------------------------------------------------------------
# 1. Falha grave na identificação pelo ISBN (Gestalt-terapia, en-dashes, CIP)
# ---------------------------------------------------------------------------

def test_cip_gestalt_terapia_with_en_dash_and_isbn():
    """Verifica se a ficha catalográfica com traços/en-dashes e cabeçalhos complexos

    extrai corretamente o título 'Gestalt-terapia: conceitos fundamentais' e o
    ISBN 978-85-323-0949-5 sem capturar 'CIP – Brasil' como título.
    """
    cip_text = """
    CIP – Brasil. Catalogação na Publicação
    Sindicato Nacional dos Editores de Livros, RJ

    G333
    Gestalt-terapia: conceitos fundamentais / Alberto Clovis de Oliveira...
    -- São Paulo : Summus, 2014.

    ISBN 978-85-323-0949-5

    1. Gestalt-terapia. 2. Psicoterapia. I. Oliveira, Alberto Clovis de.
    14-12345                                  CDD: 616.89143
    """
    res = parse_page_cip(cip_text)
    assert res is not None
    assert "Gestalt-terapia" in res["title"]
    assert res.get("subtitle") == "conceitos fundamentais"
    assert res.get("isbn") in ("9788532309495", "978-85-323-0949-5")
    assert res.get("year") == 2014
    assert res.get("publisher") == "Summus"
    assert not is_invalid_or_disclaimer_title(res["title"])


def test_cip_with_librarian_signature_footer():
    """Verifica que 'Catalogação na publicação: Bibliotecário Fulano CRB...'

    no rodapé da ficha não trunca o conteúdo da ficha catalográfica.
    """
    cip_text = """
    Dados Internacionais de Catalogação na Publicação (CIP)
    (Câmara Brasileira do Livro, SP, Brasil)

    Stein, Murray
        O mapa da alma: uma introdução à psicologia analítica / Murray Stein.
    -- São Paulo : Paulus, 2006.

    ISBN 978-85-316-0646-5

    1. Psicologia analítica 2. Jung, Carl Gustav, 1875-1961 I. Título.

    06-1234                                               CDD-150.1954
    Catalogação na publicação: Ana Paula de Souza - CRB 8/1234
    """
    res = parse_page_cip(cip_text)
    assert res is not None
    assert "mapa da alma" in res["title"].lower()
    assert res.get("isbn") in ("9788531606465", "978-85-316-0646-5")
    assert res.get("year") == 2006
    assert res.get("publisher") == "Paulus"


# ---------------------------------------------------------------------------
# 2. Organização de relatórios e instrumentos em revisão manual
# ---------------------------------------------------------------------------


def test_relatorio_manual_review_destination(tmp_path: Path):
    """Garante que relatório que exige revisão manual é encaminhado para pasta de revisão

    manual temática de Psicologia/Avaliação Psicológica/Relatórios, e NUNCA Filosofia/Artigos.
    """
    pdf_file = tmp_path / "relatorio_srs2.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 Fake PDF Content")

    meta = Metadados(
        titulo="Relatório de Avaliação SRS-2",
        area_principal="Psicologia",
        subarea="Avaliação Psicológica",
        tipo_publicacao=TipoPublicacao.RELATORIO,
        needs_review=True,
        review_reasons=["Relatório técnico que requer validação manual"],
    )

    dest_dir = tmp_path / "destino"
    res = organizar(
        metadados=meta,
        pdf_origem=pdf_file,
        destino=dest_dir,
        revisao_manual=True,
    )

    dest_str = str(res.pdf_destino)
    assert "revisao_manual" in dest_str
    assert "Psicologia" in dest_str
    assert "Avaliação Psicológica" in dest_str
    assert "Relatórios" in dest_str
    assert "Filosofia" not in dest_str
    assert "Artigos" not in dest_str


# ---------------------------------------------------------------------------
# 3. Gatekeeper temático (Outros/Geral/0.0 bloqueia organização automática)
# ---------------------------------------------------------------------------

def test_thematic_gatekeeper_blocks_zero_confidence_outros():
    """Documento com assunto Outros e confiança 0.0 NÃO pode ser organizado

    automaticamente.
    """
    enricher = MetadataEnricher(online=False)
    candidates = ExtractedCandidates(raw_title="Texto Qualquer Sem Tema")
    jev_result = JevValidationResult(
        classification="artigo",
        classification_confidence=0.8,
        assunto="Outros",
        subarea="Geral",
        subject_confidence=0.0,
        candidates=candidates,
    )

    meta = enricher.enrich(jev_result)
    assert meta.needs_review is True
    assert any("temática" in r.lower() or "outros" in r.lower() for r in meta.review_reasons)


# ---------------------------------------------------------------------------
# 4. Auditorias e metadados sincronizados; prevenção de auditorias órfãs
# ---------------------------------------------------------------------------

def test_consolidar_duplicatas_relocates_auditoria_json(tmp_path: Path):
    """Verifica que ao mover duplicatas para revisao_manual, o arquivo

    .auditoria.json é movido junto, a decisão é atualizada e não sobram órfãos.
    """
    dir_origem = tmp_path / "Psicologia" / "Geral" / "Livros"
    dir_origem.mkdir(parents=True)

    pdf_file = dir_origem / "Livro_Duplicado.pdf"
    md_file = dir_origem / "Livro_Duplicado.md"
    audit_file = dir_origem / "Livro_Duplicado.auditoria.json"

    pdf_file.write_bytes(b"%PDF-1.4")
    md_file.write_text("# Markdown Companion", encoding="utf-8")
    audit_data = {
        "nome_arquivo": "Livro_Duplicado.pdf",
        "decisao_encaminhamento": "organizado",
        "caminho_final": str(pdf_file),
        "valores_finais_selecionados": {"titulo": {"valor_escolhido": "Livro Duplicado"}},
    }
    audit_file.write_text(json.dumps(audit_data), encoding="utf-8")

    destino_base = tmp_path / "organizado"
    pipe = Pipeline(
        Config(verificar_online=False),
        OpcoesDoPipeline(destino=destino_base, mover=False),
    )
    meta1 = Metadados(
        titulo="Livro Duplicado",
        area_principal="Psicologia",
        subarea="Geral",
        tipo_publicacao=TipoPublicacao.LIVRO,
        identificadores=Identificadores(isbn="978-85-1234-567-8"),
        registro_rastreabilidade=RegistroRastreabilidade(
            nome_arquivo="Livro_Duplicado.pdf",
            caminho_final=str(pdf_file),
            decisao_encaminhamento="organizado",
        ),
    )
    meta2 = Metadados(
        titulo="Livro Duplicado 2",
        area_principal="Psicologia",
        subarea="Geral",
        tipo_publicacao=TipoPublicacao.LIVRO,
        identificadores=Identificadores(isbn="978-85-1234-567-8"),
    )
    pipe.isbns_vistos["9788512345678"] = [
        {"pdf_destino": pdf_file, "markdown_destino": md_file, "metadados": meta1},
        {"pdf_destino": pdf_file, "markdown_destino": md_file, "metadados": meta2},
    ]

    pipe.consolidar_duplicatas([])

    # O arquivo de auditoria original deve ter sido removido da origem
    assert not audit_file.exists()

    # O arquivo deve ter sido movido para revisao_manual
    rev_audits = list((destino_base / "revisao_manual").rglob("*.auditoria.json"))
    assert len(rev_audits) == 1

    audit_moved = json.loads(rev_audits[0].read_text(encoding="utf-8"))
    assert audit_moved["decisao_encaminhamento"] == "revisao_manual"
    assert "revisao_manual" in audit_moved["caminho_final"]


def test_valores_finais_selecionados_synchronization(tmp_path: Path):
    """Garante que todos os campos finais (título, autores, ano, ABNT) estão

    sincronizados em valores_finais_selecionados dentro da auditoria.
    """
    pdf_file = tmp_path / "obra.pdf"
    pdf_file.write_bytes(b"%PDF-1.4")

    meta = Metadados(
        titulo="Título Consolidado da Obra",
        autores=["Silva, João", "Souza, Maria"],
        ano=2021,
        editora="Editora Brasil",
        local="São Paulo",
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Filosofia",
        subarea="Ética",
        identificadores=Identificadores(isbn="978-85-1234-567-8"),
        referencia_abnt="SILVA, João; SOUZA, Maria. **Título Consolidado da Obra**. São Paulo: Editora Brasil, 2021.",
    )

    res = organizar(
        metadados=meta,
        pdf_origem=pdf_file,
        destino=tmp_path / "saida",
        revisao_manual=False,
    )

    audit_path = res.pdf_destino.with_suffix(".auditoria.json")
    assert audit_path.exists()

    dados_auditoria = json.loads(audit_path.read_text(encoding="utf-8"))
    vf = dados_auditoria["valores_finais_selecionados"]

    assert vf["titulo"]["valor_escolhido"] == "Título Consolidado da Obra"
    assert vf["ano"]["valor_escolhido"] == 2021
    assert vf["isbn"]["valor_escolhido"] == "978-85-1234-567-8"
    assert vf["referencia_abnt"]["valor_escolhido"].startswith("SILVA, João")
    assert dados_auditoria["caminho_final"] == str(res.pdf_destino)


# ---------------------------------------------------------------------------
# 5. Registro de consultas externas e filtro de autores institucionais
# ---------------------------------------------------------------------------

def test_institutional_authors_filter_allows_crossref_match():
    """Verifica que cabeçalhos institucionais como 'Departamento de Psicoanálisis'

    não provocam a rejeição indevida do match do Crossref para o artigo legítimo.
    """
    candidates = ExtractedCandidates(
        raw_title="Da indissociabilidade entre clínica e política",
        raw_authors=["Departamento de Psicoanálisis", "Beer, Paulo Antonio"],
        doi="10.1590/1415-4714.2018v21n4p721",
    )
    crossref_item = {
        "title": "Da indissociabilidade entre clínica e política em psicanálise",
        "authors": ["Paulo Antonio de Campos Beer", "Rodrigo Franco"],
        "doi": "10.1590/1415-4714.2018v21n4p721",
        "journal": "Revista Latinoamericana de Psicopatologia Fundamental",
        "year": 2018,
    }

    corresp, div = confirmar_mesma_obra(candidates, crossref_item, "Crossref", is_identifier_match=True)
    assert corresp is True
    assert div is None


# ---------------------------------------------------------------------------
# 6. Ausência de campos duplicados no Markdown Frontmatter
# ---------------------------------------------------------------------------

def test_markdown_frontmatter_no_duplicate_root_identifiers():
    """Garante que o frontmatter Markdown tenha apenas identificadores.isbn/doi/issn

    e não duplique isbn/doi/issn na raiz do YAML.
    """
    meta = Metadados(
        titulo="Obra Teste",
        autores=["Autor Exemplo"],
        ano=2020,
        tipo_publicacao=TipoPublicacao.LIVRO,
        identificadores=Identificadores(
            isbn="978-85-323-0949-5",
            doi="10.1234/exemplo",
            issn="1234-5678",
        ),
    )

    frontmatter = MarkdownConverter.build_yaml_frontmatter(meta)
    lines = frontmatter.splitlines()

    # Não deve ter 'isbn:', 'doi:' ou 'issn:' na raiz
    root_keys = [line.split(":")[0].strip() for line in lines if line and not line.startswith(" ") and ":" in line]
    assert "isbn" not in root_keys
    assert "doi" not in root_keys
    assert "issn" not in root_keys
    assert "identificadores" in root_keys

    # Confere dentro de identificadores
    assert "  isbn: 978-85-323-0949-5" in frontmatter
    assert "  doi: 10.1234/exemplo" in frontmatter
    assert "  issn: 1234-5678" in frontmatter


def test_abnt_reference_falls_back_to_ano_and_periodico():
    """Garante que a referência ABNT use m.ano e m.periodico quando m.year ou m.journal

    estiverem ausentes, sem gerar [20--].
    """
    meta = Metadados(
        titulo="Clínica e Política",
        autores=["Beer, Paulo"],
        tipo_publicacao=TipoPublicacao.ARTIGO,
        ano=2018,
        periodico="Revista de Psicopatologia",
    )
    ref = ABNTFormatter.format(meta)
    assert "2018" in ref
    assert "[20--]" not in ref
    assert "**Revista de Psicopatologia**" in ref


# ---------------------------------------------------------------------------
# Testes específicos para o Relatório Comparativo (teste_1 e teste_2)
# ---------------------------------------------------------------------------

def test_scanner_noise_and_camscanner_queries_blocked_and_never_confirmed():
    """1. Bloqueio de confirmação com títulos ruído de digitalização/marca d'água.

    Garante que 'Scanned by CamScanner', emails ou fragmentos de OCR:
    - São detectados como inválidos.
    - Nunca são confirmados por confirmar_mesma_obra.
    - Não disparam busca cega por título no enriquecedor.
    """
    assert is_invalid_or_disclaimer_title("Scanned by CamScanner") is True
    assert is_invalid_or_disclaimer_title("CamScanner 10-05-2026") is True
    assert is_invalid_or_disclaimer_title("APOSTILASMEDICINA@HOTMAIL.COM PRODUTOS") is True
    assert is_invalid_or_disclaimer_title("ntfc. tmn") is True

    # confirmar_mesma_obra deve rejeitar imediatamente
    cand_bogus = ExtractedCandidates(
        raw_title="Scanned by CamScanner",
        raw_authors=[],
    )
    api_indonesian_paper = {
        "title": "Analisis Sentimen Pengguna CamScanner Menggunakan Algoritma Naive Bayes",
        "authors": ["Ayunda, S.", "Pratama, B."],
        "year": 2021,
    }
    corresp, div = confirmar_mesma_obra(cand_bogus, api_indonesian_paper, "Crossref", is_identifier_match=False)
    assert corresp is False

    # MetadataEnricher não deve disparar fallback de busca para título CamScanner
    enricher = MetadataEnricher(online=False)
    jev_result = JevValidationResult(
        classification="livro",
        classification_confidence=0.85,
        assunto="Psicologia",
        subarea="Geral",
        subject_confidence=0.9,
        candidates=cand_bogus,
    )
    meta = enricher.enrich(jev_result)
    assert meta.needs_review is True
    assert any("inválido" in r.lower() or "disclaimer" in r.lower() for r in meta.review_reasons)
    # Nenhuma fonte deve estar com status CONFIRMADO
    for c in meta.fontes_consultadas:
        assert c.status != StatusConsultaProvedor.CONFIRMADO


def test_article_doi_reconciliation_indissociabilidade_full_flow():
    """2. Artigo 'Da indissociabilidade entre clínica e política':

    Garante reconciliação perfeita de DOI com autor institucional no cabeçalho
    ('Departamento de Psicoanálisis'), adoção de metadados oficiais do Crossref
    (título pleno, autores humanos, periódico, ano, DOI, páginas) e ausência de
    quarentena por falso conflito de autoria.
    """
    from unittest.mock import patch

    enricher = MetadataEnricher(online=True)
    doi = "10.17533/udea.affs.v14n27a08"

    candidates = ExtractedCandidates(
        raw_title="... clínica e po",
        raw_authors=["Departamento de Psicoanálisis"],
        doi=doi,
        sample_text="Da indissociabilidade entre clínica e política em psicanálise. Paulo Antonio de Campos Beer e Wilson Franco.",
    )
    jev_result = JevValidationResult(
        classification="artigo",
        classification_confidence=0.92,
        assunto="Psicologia",
        subarea="Psicanálise",
        subject_confidence=0.95,
        candidates=candidates,
    )

    crossref_data = {
        "title": "Da indissociabilidade entre clínica e política em psicanálise",
        "authors": ["BEER, Paulo Antonio de Campos", "FRANCO, Wilson de Albuquerque Cavalcanti"],
        "year": 2018,
        "journal": "Affectio Societatis",
        "volume": "14",
        "number": "27",
        "pages": "155-178",
        "doi": doi,
        "url": f"https://doi.org/{doi}",
    }

    with patch.object(enricher, "fetch_crossref_by_doi", return_value=crossref_data):
        meta = enricher.enrich(jev_result)

    assert meta.title == "Da indissociabilidade entre clínica e política em psicanálise"
    assert "BEER, Paulo Antonio de Campos" in meta.authors
    assert "FRANCO, Wilson de Albuquerque Cavalcanti" in meta.authors
    assert "Departamento de Psicoanálisis" not in meta.authors
    assert meta.year == 2018
    assert meta.periodico == "Affectio Societatis"
    assert meta.volume == "14"
    assert meta.number == "27"
    assert meta.pages == "155-178"
    assert meta.needs_review is False
    assert len(meta.conflitos_registrados) == 0

    # Formatação ABNT
    ref = ABNTFormatter.format(meta)
    assert "BEER, Paulo Antonio de Campos" in ref
    assert "FRANCO, Wilson de Albuquerque Cavalcanti" in ref
    assert "**Affectio Societatis**" in ref
    assert "2018" in ref
    assert "[20--]" not in ref
    assert "v. 14" in ref
    assert "n. 27" in ref
    assert "p. 155-178" in ref


def test_incomplete_metadata_separated_from_uncertain_identity():
    """3. Separação de metadado incompleto de identidade incerta.

    Livros com autor, título e classificação temática confiáveis:
    - Ausência de local (cidade) NÃO deve exigir revisão manual.
    - Ausência de ano NÃO deve exigir revisão manual isoladamente.
    - Ausência de autoria (identidade incerta) DEVE exigir revisão manual.
    """
    enricher = MetadataEnricher(online=False)

    # Obra com autor e título certos, sem cidade
    meta_sem_cidade = Metadados(
        titulo="O Conceito de Angústia",
        autores=["Kierkegaard, Søren"],
        ano=2010,
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Filosofia",
        subarea="Existencialismo",
        local=None,
    )
    jev_kierk = JevValidationResult(
        classification="livro",
        classification_confidence=0.88,
        assunto="Filosofia",
        subarea="Existencialismo",
        subject_confidence=0.92,
    )
    enricher._evaluate_needs_review(meta_sem_cidade, jev_kierk, sources_consulted=[])
    assert meta_sem_cidade.needs_review is False

    ref_sem_cidade = ABNTFormatter.format(meta_sem_cidade)
    assert "[S. l.]" in ref_sem_cidade
    assert "2010" in ref_sem_cidade

    # Obra sem autoria identificada (incerteza de identidade)
    meta_sem_autor = Metadados(
        titulo="Manual Desconhecido de Procedimentos",
        autores=[],
        ano=2015,
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Administração",
        subarea="Geral",
    )
    jev_anon = JevValidationResult(
        classification="livro",
        classification_confidence=0.80,
        assunto="Administração",
        subarea="Geral",
        subject_confidence=0.85,
    )
    enricher._evaluate_needs_review(meta_sem_autor, jev_anon, sources_consulted=[])
    assert meta_sem_autor.needs_review is True
    assert any("sem autoria" in r.lower() for r in meta_sem_autor.review_reasons)


def test_consultas_status_lifecycle_from_unconfirmed_to_confirmed():
    """4. Transparência das consultas:

    Buscas textuais por título iniciam como RESPOSTA_SEM_CONFIRMACAO
    e só são promovidas a CONFIRMADO quando a correspondência rigorosa
    for comprovada via confirmar_mesma_obra.
    """
    from unittest.mock import patch

    enricher = MetadataEnricher(online=True)

    # 1. API retorna item, mas os autores e dados não correspondem à obra do PDF
    mock_item_unrelated = {
        "title": "Gestalt Therapy in Modern Times",
        "authors": ["Unrelated Author"],
        "year": 1999,
    }
    with patch.object(enricher, "_query_google_books", return_value=mock_item_unrelated):
        jev_res = JevValidationResult(
            classification="livro",
            classification_confidence=0.85,
            assunto="Psicologia",
            subarea="Gestalt",
            subject_confidence=0.9,
            candidates=ExtractedCandidates(
                raw_title="Gestalt-terapia: conceitos fundamentais",
                raw_authors=["Alberto Clovis de Oliveira"],
            ),
        )
        meta = enricher.enrich(jev_res)

        # A consulta deve ter ficado como RESPOSTA_SEM_CONFIRMACAO, NUNCA CONFIRMADO
        gb_consultas = [c for c in meta.fontes_consultadas if c.fonte == "Google Books"]
        assert len(gb_consultas) > 0
        assert gb_consultas[-1].status == StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO

    # 2. API retorna item com correspondência rigorosa comprovada
    enricher_match = MetadataEnricher(online=True)
    mock_item_matched = {
        "title": "Gestalt-terapia: conceitos fundamentais",
        "authors": ["Alberto Clovis de Oliveira"],
        "year": 2014,
        "publisher": "Summus",
        "isbn": "9788532309495",
    }
    with patch.object(enricher_match, "_query_google_books", return_value=mock_item_matched):
        meta_match = enricher_match.enrich(jev_res)
        gb_consultas_match = [c for c in meta_match.fontes_consultadas if c.fonte == "Google Books"]
        assert len(gb_consultas_match) > 0
        assert gb_consultas_match[-1].status == StatusConsultaProvedor.CONFIRMADO


# ---------------------------------------------------------------------------
# Testes específicos para o Relatório de Auditoria Técnica (teste_3)
# ---------------------------------------------------------------------------

def test_foucault_os_anormais_isbn_canonical_anchor():
    """Garante que ISBN em contexto forte (CIP/folha de rosto) ancora metadados canônicos

    (título='Os anormais', autor='Michel Foucault', editora='Martins Fontes'), demovendo
    título de curso ('Curso no Collège de France') para subtítulo e documentando a
    divergência local em conflitos_registrados, sem rejeitar a confirmação.
    """
    from unittest.mock import patch

    candidates = ExtractedCandidates(
        raw_title="Curso no Collège de France (1974-1975)",
        raw_authors=["Mchl Focault", "Edtr WMF Martins Fontes"],
        isbn="978-85-336-1473-4",
        isbn_source="ficha_catalografica",
        is_isbn_strong=True,
        sample_text="Michel Foucault. Os anormais: Curso no Collège de France (1974-1975). São Paulo: Martins Fontes, 2001.",
    )
    jev_result = JevValidationResult(
        classification="livro",
        classification_confidence=0.92,
        assunto="Filosofia",
        subarea="Geral",
        subject_confidence=0.90,
        candidates=candidates,
    )

    api_item = {
        "title": "Os anormais",
        "authors": ["Michel Foucault"],
        "publisher": "Martins Fontes",
        "year": 2001,
        "isbn": "9788533614734",
    }

    # 1. Testar confirmar_mesma_obra diretamente
    corresp, div = confirmar_mesma_obra(candidates, api_item, "Brasil API", is_identifier_match=True)
    assert corresp is True
    assert div is not None
    assert "Divergência entre candidato local" in div

    # 2. Testar fluxo completo no MetadataEnricher
    enricher = MetadataEnricher(online=True)
    with patch.object(enricher, "fetch_brasil_api_isbn", return_value=api_item), \
         patch.object(enricher, "fetch_google_books_by_isbn", return_value=None), \
         patch.object(enricher, "fetch_openlibrary_by_isbn", return_value=None):
        meta = enricher.enrich(jev_result)

    assert meta.title == "Os anormais"
    assert meta.authors == ["Michel Foucault"]
    assert meta.publisher == "Martins Fontes"
    assert meta.subtitle == "Curso no Collège de France (1974-1975)"
    assert meta.year == 2001
    assert any("Divergência entre candidato local" in c for c in meta.conflitos_registrados)


def test_print_article_year_and_pages_preservation():
    """Garante que ano (2017) e paginação (157-179) explícitos do artigo impresso

    no PDF prevalecem sobre metadados divergentes de depósito online do Crossref (2018, 155-178),
    registrando ambos os candidatos em reconciliacao_campos.
    """
    from unittest.mock import patch

    candidates = ExtractedCandidates(
        raw_title="Da indissociabilidade entre clínica e política em psicanálise",
        raw_authors=["Beer, Paulo Antonio de Campos", "Franco, Wilson"],
        doi="10.17533/udea.affs.v14n27a08",
        raw_year=2017,
        raw_pages="157-179",
        raw_journal="Affectio Societatis",
        raw_volume="14",
        raw_number="27",
        year_source="artigo_cabecalho",
        journal_source="artigo_cabecalho",
        pages_source="artigo_cabecalho",
    )
    jev_result = JevValidationResult(
        classification="artigo",
        classification_confidence=0.95,
        assunto="Psicologia",
        subarea="Psicanálise",
        subject_confidence=0.95,
        candidates=candidates,
    )

    crossref_item = {
        "title": "Da indissociabilidade entre clínica e política em psicanálise",
        "authors": ["Paulo Antonio de Campos Beer", "Wilson Franco"],
        "journal": "Affectio Societatis",
        "volume": "14",
        "number": "27",
        "year": 2018,  # Depósito online diferente do impresso
        "pages": "155-178",  # Variação de paginação online
        "doi": "10.17533/udea.affs.v14n27a08",
    }

    enricher = MetadataEnricher(online=True)
    with patch.object(enricher, "fetch_crossref_by_doi", return_value=crossref_item), \
         patch.object(enricher, "fetch_openalex_by_doi", return_value=None), \
         patch.object(enricher, "fetch_doaj_by_doi", return_value=None), \
         patch.object(enricher, "fetch_europe_pmc_by_doi", return_value=None), \
         patch.object(enricher, "fetch_pubmed_by_doi", return_value=None):
        meta = enricher.enrich(jev_result)

    # Ano impresso prevalece
    assert meta.year == 2017
    # Paginação impressa prevalece
    assert meta.pages == "157-179"
    assert meta.periodico == "Affectio Societatis"
    assert meta.volume == "14"
    assert meta.number == "27"

    # Confere reconciliação registrada
    recon = meta.reconciliacao_campos
    assert "ano" in recon
    assert any(c.get("valor") == 2017 for c in recon["ano"].get("candidatos", []))
    assert any(c.get("valor") == 2018 for c in recon["ano"].get("candidatos", []))

    assert "paginas" in recon
    assert any(c.get("valor") == "157-179" for c in recon["paginas"].get("candidatos", []))
    assert any(c.get("valor") == "155-178" for c in recon["paginas"].get("candidatos", []))


def test_thematic_rastreabilidade_separation():
    """Garante que a resposta bruta do JEV ('humanas', 'Geral') fica preservada em

    registro_rastreabilidade.resposta_jev sem ser contaminada pelo refinamento local
    (Psicologia / Psicanálise), e que a justificativa temática é registrada.
    """
    candidates = ExtractedCandidates(
        raw_title="Da indissociabilidade entre clínica e política em psicanálise",
        raw_authors=["Paulo Beer"],
        sample_text="Artigo de psicanálise e teoria freudiana.",
    )
    jev_result = JevValidationResult(
        classification="artigo",
        classification_confidence=0.90,
        assunto="humanas",
        subarea="Geral",
        subject_confidence=0.80,
        justificativa="Classificado pelo JEV como ciências humanas geral",
        candidates=candidates,
    )

    enricher = MetadataEnricher(online=False)
    meta = enricher.enrich(jev_result)

    # Resposta bruta do JEV intacta
    assert meta.registro_rastreabilidade.resposta_jev["assunto"] == "humanas"
    assert meta.registro_rastreabilidade.resposta_jev["subarea"] == "Geral"
    assert "ciências humanas geral" in meta.registro_rastreabilidade.resposta_jev["justificativa"]

    # Decisão final refinada
    assert meta.area_principal == "Psicologia"
    assert meta.subarea == "Psicanálise"
    assert "psicanálise" in meta.justificativa_classificacao_tematica.lower()

    # Sincronização em valores_finais_selecionados
    vf = meta.registro_rastreabilidade.valores_finais_selecionados
    assert vf["area_principal"]["valor_escolhido"] == "Psicologia"
    assert vf["subarea"]["valor_escolhido"] == "Psicanálise"
    assert vf["justificativa_classificacao_tematica"]["valor_escolhido"] == meta.justificativa_classificacao_tematica


def test_frontmatter_periodico_unified_and_non_null(tmp_path: Path):
    """Garante que tanto converter.py quanto organizer.py geram frontmatter YAML

    com 'periodico' preenchido (não nulo) quando presente nos metadados, coincidindo
    com a referência ABNT e a auditoria.
    """
    meta = Metadados(
        titulo="Da indissociabilidade entre clínica e política em psicanálise",
        autores=["Beer, Paulo Antonio de Campos"],
        tipo_publicacao=TipoPublicacao.ARTIGO,
        ano=2017,
        periodico="Affectio Societatis",
        volume="14",
        numero="27",
        paginas="157-179",
        area_principal="Psicologia",
        subarea="Psicanálise",
        justificativa_classificacao_tematica="Identificado termo psicanálise",
        referencia_abnt="BEER, Paulo Antonio de Campos. Da indissociabilidade entre clínica e política em psicanálise. **Affectio Societatis**, v. 14, n. 27, p. 157-179, 2017.",
    )

    # 1. converter.py
    fm_converter = MarkdownConverter.build_yaml_frontmatter(meta)
    assert "periodico: Affectio Societatis" in fm_converter
    assert "periodico: null" not in fm_converter
    assert "volume: '14'" in fm_converter or "volume: 14" in fm_converter
    assert "numero: '27'" in fm_converter or "numero: 27" in fm_converter
    assert "paginas: 157-179" in fm_converter
    assert "justificativa_classificacao_tematica: Identificado termo psicanálise" in fm_converter

    # 2. organizer.py
    md_content = gerar_markdown(meta, arquivo_origem=Path("dummy.pdf"))
    assert "periodico: Affectio Societatis" in md_content
    assert "periodico: null" not in md_content
    assert "volume: '14'" in md_content or "volume: 14" in md_content
    assert "numero: '27'" in md_content or "numero: 27" in md_content
    assert "paginas: 157-179" in md_content


def test_duplicata_isbn_mesma_edicao_sem_falsa_quarentena(tmp_path: Path):
    """Garante que múltiplos arquivos com o mesmo ISBN e mesma obra (título/autor/edição),

    como no caso Fukumitsu, NÃO são sumariamente postos em quarentena ou marcados como
    erro de classificação.
    """
    pdf1 = tmp_path / "Fukumitsu_Copia1.pdf"
    pdf2 = tmp_path / "Fukumitsu_Copia2.pdf"
    md1 = tmp_path / "Fukumitsu_Copia1.md"
    md2 = tmp_path / "Fukumitsu_Copia2.md"

    # Conteúdo binário ligeiramente diferente (ex: scans ou anotações distintas) para não ser hash idêntico
    pdf1.write_bytes(b"%PDF-1.4 Scan A")
    pdf2.write_bytes(b"%PDF-1.4 Scan B")
    md1.write_text("# Fukumitsu", encoding="utf-8")
    md2.write_text("# Fukumitsu", encoding="utf-8")

    meta1 = Metadados(
        titulo="Suicídio e Gestalt-terapia",
        autores=["Fukumitsu, Karina Okajima"],
        ano=2014,
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Psicologia",
        subarea="Gestalt",
        identificadores=Identificadores(isbn="978-85-1234-999-9"),
        registro_rastreabilidade=RegistroRastreabilidade(
            nome_arquivo="Fukumitsu_Copia1.pdf",
            caminho_final=str(pdf1),
            decisao_encaminhamento="organizado",
        ),
    )
    meta2 = Metadados(
        titulo="Suicídio e Gestalt-terapia",
        autores=["Fukumitsu, Karina Okajima"],
        ano=2014,
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Psicologia",
        subarea="Gestalt",
        identificadores=Identificadores(isbn="978-85-1234-999-9"),
        registro_rastreabilidade=RegistroRastreabilidade(
            nome_arquivo="Fukumitsu_Copia2.pdf",
            caminho_final=str(pdf2),
            decisao_encaminhamento="organizado",
        ),
    )

    destino_base = tmp_path / "saida"
    pipe = Pipeline(
        Config(verificar_online=False),
        OpcoesDoPipeline(destino=destino_base, mover=False),
    )
    pipe.isbns_vistos["9788512349999"] = [
        {"pdf_destino": pdf1, "markdown_destino": md1, "metadados": meta1},
        {"pdf_destino": pdf2, "markdown_destino": md2, "metadados": meta2},
    ]

    pipe.consolidar_duplicatas([])

    # NÃO devem ser marcados com needs_review nem movidos para revisao_manual
    assert meta1.needs_review is False
    assert meta2.needs_review is False
    assert any("mesma edição" in a for a in meta1.avisos)
    assert any("mesma edição" in a for a in meta2.avisos)


def test_openalex_error_and_cache_transparency():
    """Garante que erros de taxa (HTTP 429) no OpenAlex são explicitamente registrados

    em erro_ou_aviso com a mensagem informativa, e que o cache preserva esse status e
    mensagem nas chamadas subsequentes, garantindo rastreabilidade no auditoria.json.
    """
    from unittest.mock import patch, MagicMock

    enricher = MetadataEnricher(online=True)

    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429

    with patch.object(enricher.session, "get", return_value=mock_resp_429):
        res1 = enricher.fetch_openalex_by_title("Título Teste OpenAlex Rate Limit")
        assert res1 is None

    # Primeira consulta deve ter erro_ou_aviso explícito
    assert len(enricher.consultas_realizadas) >= 1
    c1 = enricher.consultas_realizadas[-1]
    assert c1.fonte == "OpenAlex"
    assert c1.status == StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
    assert "HTTP 429: Limite de taxa" in c1.erro_ou_aviso

    # Segunda consulta para o mesmo título deve bater no cache e TAMBÉM registrar na lista de fontes
    initial_count = len(enricher.consultas_realizadas)
    res2 = enricher.fetch_openalex_by_title("Título Teste OpenAlex Rate Limit")
    assert res2 is None
    assert len(enricher.consultas_realizadas) == initial_count + 1
    c2 = enricher.consultas_realizadas[-1]
    assert c2.fonte == "OpenAlex"
    assert c2.status == StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
    assert "HTTP 429: Limite de taxa" in c2.erro_ou_aviso


# ---------------------------------------------------------------------------
# Testes de regressão específicos para o Parecer Técnico do Teste 4
# ---------------------------------------------------------------------------

def test_foucault_author_synchronization_and_frontmatter(tmp_path):
    """Garante que 'autor_principal' é estritamente sincronizado com o autor canônico

    (Michel Foucault) e que ruídos de OCR (ex: 'Fran,ois Ewald') são completamente
    eliminados dos modelos e do frontmatter Markdown.
    """
    # 1. Metadados inicializados com ruído em autor_principal, mas autores corrigidos
    meta = Metadados(
        titulo="Os anormais",
        subtitulo="curso no Collège de France (1974-1975)",
        autores=["Michel Foucault"],
        autor_principal="Fran,ois Ewald",
        editora="Martins Fontes",
        ano=2001,
        area_principal="Filosofia",
        subarea="Filosofia Contemporânea",
        tipo_publicacao=TipoPublicacao.LIVRO,
    )
    # autor_para_nome e autor_principal devem ser sincronizados
    assert meta.autor_para_nome == "Michel Foucault"
    assert meta.autor_principal == "Michel Foucault"

    # 2. Reconciliação via _merge_metadata também deve sincronizar autor_principal
    enricher = MetadataEnricher(online=False)
    extra = {"authors": ["Michel Foucault"]}
    enricher._merge_metadata(meta, extra, authoritative=True, provider="Brasil API / CBL")
    assert meta.autores == ["Michel Foucault"]
    assert meta.autor_principal == "Michel Foucault"

    # 3. No Markdown gerado, autor_principal deve ser Foucault, nunca François Ewald
    pdf_fake = tmp_path / "Os_Anormais_Foucault.pdf"
    pdf_fake.write_bytes(b"%PDF-1.4 fake")
    md_content = gerar_markdown(meta, total_paginas=400, arquivo_origem=pdf_fake)
    assert "autor_principal: Michel Foucault" in md_content
    assert "Fran,ois Ewald" not in md_content


def test_foucault_subtitle_course_period_not_publication_year():
    """Garante que intervalos de curso em subtítulo como '1974-1975' em Os Anormais

    não são capturados como ano de publicação do artigo/livro (evitando falso conflito
    PDF=1974 vs OpenLibrary=2001).
    """
    from organizador_pdf.classifier_jev import parse_article_header_citation

    # Texto contendo subtítulo do curso
    sample = "Os Anormais: Curso no Collège de France (1974-1975). Michel Foucault."
    art_meta = parse_article_header_citation(sample)
    # Não deve capturar 1974 como ano de artigo
    assert art_meta is None or art_meta.get("year") is None

    # Teste de reconciliação com ISBN autoritativo (2001)
    enricher = MetadataEnricher(online=False)
    cand = ExtractedCandidates(
        raw_title="Os anormais",
        raw_subtitle="curso no Collège de France (1974-1975)",
        raw_year=1974,
        year_source="artigo_cabecalho",
    )
    meta = PublicationMetadata(
        title="Os anormais",
        subtitle="curso no Collège de France (1974-1975)",
        year=1974,
        classification="livro",
        identifiers=Identificadores(isbn="9788533614536"),
    )
    extra_ol = {"year": 2001, "title": "Os anormais"}
    enricher._merge_metadata(meta, extra_ol, authoritative=True, provider="OpenLibrary", candidates=cand)

    assert meta.year == 2001
    # Não deve ter falso conflito 'PDF=1974 vs OpenLibrary=2001' registrado
    assert not any("PDF=1974 vs OpenLibrary=2001" in c for c in meta.conflitos_registrados)


def test_schultz_prose_paragraph_title_rejection_and_fallback():
    """Verifica que frases de prosa como 'O tema deste livro é a história da psicologia moderna...'
    ou 'experimental. Embora não ignoremos...' são rejeitadas por is_prose_or_paragraph_title,
    e que pronomes ou conceitos isolados ('Nos', 'Autismo', 'Homenagem', 'Português') não são aceitos como autores.
    """
    from organizador_pdf.classifier_jev import is_prose_or_paragraph_title, is_likely_person_name

    prose_sample = (
        "O tema deste livro é a história da psicologia moderna, aquele período "
        "que se inicia no final do século XIX, quando a psicologia se tornou uma "
        "disciplina independente."
    )
    assert is_prose_or_paragraph_title(prose_sample) is True
    assert is_invalid_or_disclaimer_title(prose_sample) is True

    prose_sample_2 = "experimental. Embora não ignoremos o pensamento filosófico anterior, concentramo-nos"
    assert is_prose_or_paragraph_title(prose_sample_2) is True
    assert is_invalid_or_disclaimer_title(prose_sample_2) is True

    # Palavras isoladas, pronomes ou substantivos não são pessoas
    assert is_likely_person_name("Nos") is False
    assert is_likely_person_name("Autismo") is False
    assert is_likely_person_name("Homenagem") is False
    assert is_likely_person_name("Português") is False
    assert is_likely_person_name("Abdul") is False
    assert is_likely_person_name("Schultz e Schultz") is True

    # Se um título não tiver evidência estrutural e confiança < 0.75 sem identificador, deve exigir revisão
    meta = PublicationMetadata(
        title="História da Psicologia Moderna",
        authors=["Duane P. Schultz", "Sydney Ellen Schultz"],
        classification="livro",
        title_source="texto_nativo",
        city=None,
    )
    enricher = MetadataEnricher(online=False)
    enricher._evaluate_needs_review(meta, sources_consulted=[])
    assert meta.needs_review is True
    assert any("sem validação estrutural" in r.lower() or "confiança de metadados insuficiente" in r.lower() for r in meta.review_reasons)


def test_fukumitsu_bibliographic_role_and_abnt_formatting():
    """Verifica que a função editorial/organização é modelada separadamente do nome

    da pessoa, gerando 'FRAZÃO, Lilian Meyer; FUKUMITSU, Karina Okajima (org.).'
    em vez de prefixar 'organização' ao nome.
    """
    cip_sample = """
    Dados Internacionais de Catalogação na Publicação (CIP)
    F961g
    Gestalt-terapia : conceitos fundamentais / organização Lilian Meyer Frazão e Karina Okajima Fukumitsu. -- São Paulo : Summus, 2014.
    ISBN 978-85-323-0949-5
    1. Gestalt-terapia 2. Psicoterapia I. Frazão, Lilian Meyer. II. Fukumitsu, Karina Okajima.
    """
    cip_meta = parse_page_cip(cip_sample)
    assert cip_meta is not None
    assert cip_meta.get("papel_autoria") == "organizador"
    # Autores devem estar higienizados sem 'organização'
    for a in cip_meta["authors"]:
        assert "organização" not in a.lower()
        assert "organizacao" not in a.lower()

    # Formatação ABNT
    meta = PublicationMetadata(
        title="Gestalt-terapia",
        subtitle="conceitos fundamentais",
        authors=["Lilian Meyer Frazão", "Karina Okajima Fukumitsu"],
        papel_autoria="organizador",
        publisher="Summus",
        city="São Paulo",
        year=2014,
        classification="livro",
    )
    abnt = ABNTFormatter.format(meta)
    assert "(org.)." in abnt
    assert "FRAZÃO, Lilian Meyer; FUKUMITSU, Karina Okajima (org.)." in abnt
    assert "organização Lilian" not in abnt


def test_gestalt_thematic_area_and_cnpq_hierarchy():
    """Verifica que descritores de Gestalt-Terapia mapeiam para Área: Psicologia / Subárea: Gestalt-Terapia,

    respeitando estritamente a hierarquia do CNPq e evitando 'Outros / Geral' ou pastas de topo anômalas.
    """
    # 1. Título clínico com adolescentes
    area1, sub1, rule1 = infer_thematic_area(
        title="A Clínica Gestáltica com Adolescentes",
        authors=["Karina Okajima Fukumitsu"],
        sample_text="Abordagem gestáltica na psicoterapia com jovens e adolescentes.",
        return_rule=True,
    )
    assert area1 == "Psicologia"
    assert sub1 == "Gestalt-Terapia"
    assert rule1 == "descritores_gestalt_terapia"

    # 2. CIP com Gestalt-Terapia mapeia para subárea sob Psicologia
    area2, sub2, rule2 = infer_thematic_area(
        title="Gestalt-terapia: conceitos fundamentais",
        cand_area="Gestalt-terapia",
        return_rule=True,
    )
    assert area2 == "Psicologia"
    assert sub2 == "Gestalt-Terapia"

    # 3. Foucault no Collège de France mapeia para Filosofia Contemporânea
    area3, sub3, rule3 = infer_thematic_area(
        title="Os anormais",
        subtitle="curso no Collège de France (1974-1975)",
        authors=["Michel Foucault"],
        return_rule=True,
    )
    assert area3 == "Filosofia"
    assert sub3 == "Filosofia Contemporânea"


def test_openalex_throttle_circuit_breaker_and_deduplication():
    """Verifica que quando o Crossref já confirmou o DOI, a consulta ao OpenAlex é dispensada;

    e que sob HTTP 429, o circuit breaker é acionado evitando requisições de rede contínuas.
    """
    from unittest.mock import patch, MagicMock

    enricher = MetadataEnricher(online=True)

    # 1. Deduplicação: se Crossref já confirmou o DOI
    cand = ExtractedCandidates(doi="10.1590/fake-doi")
    meta = PublicationMetadata(classification="artigo", identifiers=Identificadores(doi="10.1590/fake-doi"))

    mock_cr = {
        "title": "Artigo Clínico em Psicanálise",
        "authors": ["Autor Teste"],
        "year": 2020,
        "journal": "Revista de Psicologia",
        "doi": "10.1590/fake-doi",
    }
    jev = JevValidationResult(candidates=cand, classification="artigo")
    with patch.object(enricher, "fetch_crossref_by_doi", return_value=mock_cr), \
         patch.object(enricher, "fetch_openalex_by_doi") as mock_oa:
        enricher.enrich(jev)
        # OpenAlex NÃO deve ser chamado pois Crossref já confirmou o DOI
        mock_oa.assert_not_called()
        assert enricher.provider_status.get("openalex") == StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO

    # 2. Circuit breaker sob 429
    MetadataEnricher._openalex_circuit_breaker_until = 0.0
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429

    with patch.object(enricher.session, "get", return_value=mock_resp_429) as mock_get:
        # Primeira chamada recebe 429 e aciona circuit breaker
        res1 = enricher.fetch_openalex_by_doi("10.1234/test-cb")
        assert res1 is None
        assert MetadataEnricher._openalex_circuit_breaker_until > time.time()
        assert mock_get.call_count == 1

        # Segunda chamada com DOI diferente NÃO deve fazer get na rede
        res2 = enricher.fetch_openalex_by_doi("10.1234/test-cb-2")
        assert res2 is None
        # get na rede não foi chamado novamente
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Testes específicos para o Parecer Técnico do Teste 5
# ---------------------------------------------------------------------------




def test_p1_fenomenologia_thematic_separation_from_biography():
    """P1: Menção a especialização em Gestalt-terapia na biografia do autor não

    pode desviar obra com título de Fenomenologia e Humanismo para Gestalt-Terapia.
    """
    from organizador_pdf.metadata_api import infer_thematic_area

    bio_sample = (
        "ADRIANO FURTADO HOLANDA\n"
        "Pós-doutor em Psicologia pela Universidade de Brasília - UnB.\n"
        "Doutor em Psicologia pela PUC-Campinas.\n"
        "Especialista em Gestalt-terapia pelo Instituto de Gestalt-terapia de Brasília.\n"
        "Docente do Departamento de Psicologia Geral e do Desenvolvimento."
    )

    area, subarea, rule = infer_thematic_area(
        title="Fenomenologia e Humanismo",
        subtitle="Reflexões Necessárias",
        authors=["Adriano Furtado Holanda"],
        sample_text=bio_sample,
        return_rule=True,
    )

    assert area == "Psicologia"
    assert subarea == "Fenomenologia"
    assert rule == "descritores_fenomenologia"
    assert subarea != "Gestalt-Terapia"


def test_p1_cip_organizers_preserved_when_api_authors_empty():
    """P1: Quando API externa retorna autores vazios (ex: Brasil API / CBL),

    preserva os organizadores extraídos da ficha catalográfica (CIP) local com papel_autoria='organizador'.
    """
    from organizador_pdf.classifier_jev import parse_page_cip

    cip_text = (
        "CIP-BRASIL. CATALOGAÇÃO NA PUBLICAÇÃO\n"
        "SINDICATO NACIONAL DOS EDITORES DE LIVROS, RJ\n"
        "E46\n"
        "Enfrentando crises e fechando gestalten [recurso eletrônico] / organização Lilian Meyer\n"
        "Frazão, Karina Okajima Fukumitsu. - 1. ed. - São Paulo : Summus, 2020.\n"
        "recurso digital (Gestalt-terapia: fundamentos e práticas ; 7)\n"
        "ISBN 978-85-323-1153-5\n"
        "1. Psicoterapia. 2. Gestalt-terapia. I. Frazão, Lilian Meyer. II. Fukumitsu, Karina Okajima. III. Série.\n"
    )

    cip_meta = parse_page_cip(cip_text)
    assert cip_meta is not None
    assert cip_meta["title"] == "Enfrentando crises e fechando gestalten"
    assert cip_meta["papel_autoria"] == "organizador"
    assert any("Frazão" in a for a in cip_meta["authors"])
    assert any("Fukumitsu" in a for a in cip_meta["authors"])

    # Simula enriquecedor recebendo retorno de API com authors: []
    enricher = MetadataEnricher(online=False)
    cand = ExtractedCandidates(
        raw_title=cip_meta["title"],
        raw_authors=cip_meta["authors"],
        papel_autoria=cip_meta["papel_autoria"],
        raw_publisher="Summus",
        raw_year=2020,
        raw_city="São Paulo",
        isbn="9788532311535",
    )
    jev_res = JevValidationResult(candidates=cand, classification="livro", classification_confidence=0.95)
    meta = enricher.enrich(jev_res)

    # Simula mesclagem de retorno de API com authors vazio
    enricher._merge_metadata(
        meta,
        {"title": "Enfrentando crises e fechando gestalten", "authors": [], "publisher": "Summus Editorial"},
        authoritative=True,
        provider="brasil_api",
        candidates=cand,
    )

    assert meta.authors == cip_meta["authors"]
    assert meta.papel_autoria == "organizador"
    assert not any("sem autoria identificada" in r.lower() for r in meta.review_reasons)


def test_p2_jung_volume_not_mistaken_for_title():
    """P2: Nomes de arquivo ou linhas como 'Vol 16' devem ser tratados como volume/série

    e nunca substituir o título real da obra ('A Prática da psicoterapia').
    """
    from organizador_pdf.classifier_jev import parse_filename_extended, is_invalid_or_disclaimer_title

    # 1. Parse de nome de arquivo multi-parte
    fn = "C. G. Jung - Vol 16 - A PRÁTICA DA PSICOTERAPIA.pdf"
    parsed = parse_filename_extended(fn)
    assert parsed["author"] == "C. G. Jung"
    assert parsed["volume"] == "Vol 16"
    assert "PRÁTICA DA PSICOTERAPIA" in parsed["title"]

    # 2. 'Vol 16' é rejeitado como título
    assert is_invalid_or_disclaimer_title("Vol 16") is True
    assert is_invalid_or_disclaimer_title("Volume 2") is True
    assert is_invalid_or_disclaimer_title("Tomo 1") is True


def test_staged_ocr_early_exit_on_first_stage(tmp_path: Path):
    """Garante que o OCR em etapas interrompe na Etapa 1 quando o texto extraído é suficiente."""
    from organizador_pdf.converter import converter_pdf
    import pymupdf

    pdf_file = tmp_path / "livro_escaneado_teste.pdf"
    doc = pymupdf.open()
    # Cria 20 páginas em branco (simulando PDF escaneado)
    for _ in range(20):
        doc.new_page(width=595, height=842)
    doc.save(str(pdf_file))
    doc.close()

    from unittest.mock import patch

    # Mock executar_ocr_seletivo para retornar texto suficiente já na Etapa 1
    with patch("organizador_pdf.converter.executar_ocr_seletivo") as mock_ocr:
        mock_ocr.return_value = (
            "--- [Página 1 - OCR] ---\n"
            "Título: Gestalt-terapia Explicada\n"
            "Autor: Frederick S. Perls\n"
            "Editora Summus Editorial, São Paulo, 1977.\n",
            [1],
        )
        doc_conv = converter_pdf(pdf_file)
        # Deve ter sido chamado apenas uma vez (Etapa 1), não 4 vezes
        assert mock_ocr.call_count == 1
        assert doc_conv.extracao_texto is not None
        assert doc_conv.extracao_texto.ocr_aplicado is True
        assert doc_conv.extracao_texto.suficiente is True



