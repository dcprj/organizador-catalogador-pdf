"""Testes offline do motor taxonômico (Assunto Principal -> Especificação -> Tipo)."""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from organizador_pdf.classifier_jev import parse_page_cip
from organizador_pdf.converter import MarkdownConverter
from organizador_pdf.metadata_api import MetadataEnricher, infer_thematic_area
from organizador_pdf.models import (
    ExtractedCandidates,
    JevValidationResult,
    PublicationMetadata,
)
from organizador_pdf.organizer import PipelineOrganizer, montar_diretorio

REPO_ROOT = Path(__file__).resolve().parent.parent


def _infer(**kw):
    kw.setdefault("return_rule", True)
    return infer_thematic_area(**kw)


# 1. Neuropsicologia, incluindo imunidade a ruído do prefácio
def test_neuropsicologia_por_titulo():
    area, sub, rule = _infer(
        title="Neuropsicologia", subtitle="teoria e prática", authors=["FUENTES, Daniel"]
    )
    assert (area, sub, rule) == ("Psicologia", "Neuropsicologia", "descritores_neuropsicologia")


def test_neuropsicologia_imune_a_ruido_de_psicanalise_no_prefacio():
    prefacio = (
        "Prefácio. O autor dialoga com Freud e com a Psicanálise em duas passagens, "
        "mas o livro trata da avaliação das funções executivas."
    )
    area, sub, rule = _infer(
        title="Neuropsicologia", subtitle="teoria e prática", authors=["FUENTES, Daniel"],
        sample_text=prefacio,
    )
    assert (area, sub) == ("Psicologia", "Neuropsicologia")
    assert rule == "descritores_neuropsicologia"


# 2. Subáreas de Psicologia
@pytest.mark.parametrize(
    "title,authors,subarea",
    [
        ("Terapia Cognitivo-Comportamental na prática", ["Judith Beck"], "Terapia Cognitivo-Comportamental"),
        ("Análise do Comportamento aplicada", ["Fred Keller"], "Análise do Comportamento"),
        ("Psicopatologia e semiologia dos transtornos mentais", ["Paulo Dalgalarrondo"], "Psicopatologia"),
        ("Psicologia Jurídica e perícia psicológica", [], "Psicologia Jurídica"),
        ("Psicologia Organizacional e do Trabalho", ["Christophe Dejours"], "Psicologia Organizacional e do Trabalho"),
        ("Psicodrama: teatro da espontaneidade", ["J. L. Moreno"], "Psicodrama"),
        ("Logoterapia e análise existencial", ["Viktor Frankl"], "Logoterapia e Análise Existencial"),
        ("Autismo e transtorno do espectro autista", [], "Autismo e Neurodesenvolvimento"),
    ],
)
def test_subareas_de_psicologia(title, authors, subarea):
    area, sub, _ = _infer(title=title, authors=authors)
    assert (area, sub) == ("Psicologia", subarea)


# 3. Administração
def test_marketing_por_titulo():
    assert _infer(title="Administração de Marketing", authors=["Philip Kotler"])[:2] == (
        "Administração", "Marketing",
    )


def test_marketing_por_cip():
    area, sub, _ = _infer(title="Fundamentos", cand_area="Administração", external_subjects=["Marketing"])
    assert (area, sub) == ("Administração", "Marketing")


def test_marketing_por_cdd():
    area, sub, _ = _infer(title="Fundamentos", cdd="658.8")
    assert (area, sub) == ("Administração", "Marketing")


def test_gestao_de_pessoas_e_financas():
    assert _infer(title="Gestão de Pessoas", authors=["Idalberto Chiavenato"])[:2] == (
        "Administração", "Gestão de Pessoas",
    )
    assert _infer(title="Finanças corporativas e contabilidade gerencial")[:2] == (
        "Administração", "Finanças e Contabilidade",
    )


# 4. Outras áreas
@pytest.mark.parametrize(
    "kwargs,esperado",
    [
        (dict(title="Direito Penal brasileiro"), ("Direito", "Direito Penal e Criminologia")),
        (dict(title="Compêndio de Psiquiatria", authors=["Harold Kaplan"]), ("Ciências da Saúde", "Psiquiatria")),
        (dict(title="Neuroanatomia funcional", authors=["Angelo Machado"]),
         ("Ciências da Saúde", "Neurociências e Neuroanatomia")),
        (dict(title="Metodologia Científica", authors=["Eva Maria Lakatos"]), ("Educação", "Metodologia Científica")),
        (dict(title="Antropologia cultural", authors=["Clifford Geertz"]), ("Ciências Sociais", "Antropologia")),
        (dict(title="Inteligência Artificial e aprendizado de máquina"),
         ("Computação", "Inteligência Artificial e Ciência de Dados")),
    ],
)
def test_outras_areas(kwargs, esperado):
    area, sub, _ = _infer(**kwargs)
    assert (area, sub) == esperado


def test_fallback_default_sem_evidencias():
    assert _infer(title="Xyzzy plugh") == ("Outros", "Geral", "fallback_default")


# 5. parse_page_cip: múltiplos assuntos e CDD
def test_parse_page_cip_extrai_assuntos_e_cdd():
    cip = (
        "Dados Internacionais de Catalogação na Publicação (CIP)\n"
        "Kotler, Philip\n"
        "Administração de marketing / Philip Kotler. - São Paulo : Atlas, 2019.\n"
        "1. Administração. 2. Marketing. 3. Comportamento do consumidor. I. Título.\n"
        "CDD 658.8\n"
    )
    res = parse_page_cip(cip)
    assert res is not None
    assert res["area"] == "Administração"
    assert res["subjects"] == ["Administração", "Marketing", "Comportamento do consumidor"]
    assert res["cdd"] == "658.8"


# 6. Reavaliação pós-API usando subjects/categories
def test_enrich_reavalia_tema_com_subjects_das_apis():
    enricher = MetadataEnricher(online=True)
    cand = ExtractedCandidates(
        isbn="9788520011508", raw_title="Fundamentos", raw_authors=["Autor Teste"], sample_text=""
    )
    jev = JevValidationResult(candidates=cand, classification="livro")
    api = {
        "title": "Fundamentos",
        "authors": ["Autor Teste"],
        "isbn": "9788520011508",
        "subjects": ["Marketing", "Administração"],
    }
    with patch.object(enricher, "fetch_brasil_api_isbn", return_value=api), \
         patch.object(enricher, "fetch_google_books_by_isbn", return_value=None), \
         patch.object(enricher, "fetch_openlibrary_by_isbn", return_value=None), \
         patch.object(enricher, "fetch_google_books_by_title", return_value=None), \
         patch.object(enricher, "fetch_openlibrary_by_title", return_value=None), \
         patch.object(enricher, "fetch_crossref_by_title", return_value=None), \
         patch.object(enricher, "fetch_openalex_by_title", return_value=None), \
         patch.object(enricher, "fetch_google_scholar", return_value=None):
        meta = enricher.enrich(jev)
    assert (meta.area_principal, meta.subarea) == ("Administração", "Marketing")
    assert meta.regra_classificacao_tematica == "descritores_marketing"


def test_parsers_de_api_preservam_subjects():
    enricher = MetadataEnricher(online=False)
    cr = enricher._parse_crossref_item({"title": ["T"], "subject": ["Psychiatry", "Neurology"]})
    assert cr["subjects"] == ["Psychiatry", "Neurology"]
    gb = enricher._parse_google_books_item({"title": "T", "categories": ["Psychology / Clinical"]})
    assert "Psychology" in gb["subjects"]
    oa = enricher._parse_openalex_item(
        {"title": "T", "primary_topic": {"display_name": "Clinical Psychology"},
         "concepts": [{"display_name": "Psychotherapy"}]}
    )
    assert oa["subjects"] == ["Clinical Psychology", "Psychotherapy"]


# 7. Estrutura padrão cnpq e --plana
def test_default_cnpq_no_main_e_no_organizer():
    sys.path.insert(0, str(REPO_ROOT))
    try:
        main = importlib.import_module("main")
    finally:
        sys.path.remove(str(REPO_ROOT))
    parser = main.build_parser()
    base = ["--input", "origem", "--output", "destino"]
    assert parser.parse_args(base).estrutura == "cnpq"
    assert parser.parse_args(base + ["--plana"]).estrutura == "plana"
    assert parser.parse_args(base + ["--cnpq"]).estrutura == "cnpq"
    assert PipelineOrganizer().estrutura == "cnpq"


def test_montar_diretorio_hierarquico_e_plano(tmp_path):
    from organizador_pdf.models import Metadados, TipoPublicacao

    meta = Metadados(
        titulo="Neuropsicologia",
        autores=["FUENTES, Daniel"],
        area_principal="Psicologia",
        subarea="Neuropsicologia",
        tipo_publicacao=TipoPublicacao.LIVRO,
    )
    pdf_dir, _ = montar_diretorio(tmp_path, meta)
    assert pdf_dir == tmp_path / "Psicologia" / "Neuropsicologia" / "Livros"
    pdf_dir_plana, _ = montar_diretorio(tmp_path, meta, estrutura="plana")
    assert pdf_dir_plana == tmp_path / "livro"


# 8. Frontmatter sempre com area_principal e subarea
def test_frontmatter_sempre_tem_area_e_subarea():
    meta = PublicationMetadata(title="Sem Área", classification="livro")
    fm = MarkdownConverter.build_yaml_frontmatter(meta)
    assert "area_principal: Outros" in fm
    assert "subarea: " in fm
    assert "subarea: null" not in fm and "subarea: None" not in fm
    assert "subarea/" not in fm

    meta2 = PublicationMetadata(
        title="Neuropsicologia", classification="livro",
        area_principal="Psicologia", area="Psicologia", subarea="Neuropsicologia",
    )
    fm2 = MarkdownConverter.build_yaml_frontmatter(meta2)
    assert "area_principal: Psicologia" in fm2
    assert "subarea: Neuropsicologia" in fm2
    assert "subarea/neuropsicologia" in fm2
    assert "area/psicologia" in fm2


# ----------------------------------------------------------------------------- Wave 2
@pytest.mark.parametrize(
    "title,esperado",
    [
        ("Avaliacao-Neuropsicologica-Leandro-F-Malloy-Diniz", ("Psicologia", "Neuropsicologia")),
        ("01-Os-Pré-socraticos-Coleção-Os-Pensadores-1996", ("Filosofia", "História da Filosofia")),
        ("A-ATUACAO-DO-PSICOLOGO-ENQUANTO-MEDIADOR_DE", ("Psicologia", "Psicologia Jurídica")),
    ],
)
def test_hifens_e_underscores_em_titulos(title, esperado):
    assert _infer(title=title)[:2] == esperado


@pytest.mark.parametrize(
    "kwargs,esperado",
    [
        (dict(title="101 Técnicas da Terapia Cognitivo-Comportamental",
              external_subjects=["Psicologia", "Psiquiatria"], cdd="616.89142"),
         ("Psicologia", "Terapia Cognitivo-Comportamental")),
        (dict(title="Além do princípio do prazer", authors=["Sigmund Freud"],
              external_subjects=["Psicologia", "Psiquiatria"], cdd="616.89"),
         ("Psicologia", "Psicanálise")),
        (dict(title="Avaliação Psicológica da Inteligência",
              external_subjects=["Psiquiatria", "Psicologia"]),
         ("Psicologia", "Avaliação Psicológica")),
        (dict(title="Compêndio de Psiquiatria", authors=["Harold Kaplan", "Benjamin Sadock"],
              external_subjects=["Psiquiatria"], cdd="616.89"),
         ("Ciências da Saúde", "Psiquiatria")),
    ],
)
def test_amortecimento_co_assunto_psiquiatria(kwargs, esperado):
    assert _infer(**kwargs)[:2] == esperado


@pytest.mark.parametrize(
    "kwargs,esperado",
    [
        (dict(title="Executive Functions in Adults",
              external_subjects=["Psychology", "Neuropsychology", "Neuroscience"]),
         ("Psicologia", "Neuropsicologia")),
        (dict(title="Consumer Decisions", external_subjects=["Business", "Marketing"]),
         ("Administração", "Marketing")),
    ],
)
def test_aliases_de_assuntos_em_ingles(kwargs, esperado):
    assert _infer(**kwargs)[:2] == esperado


def test_contar_termos_deduplica_singular_plural():
    from organizador_pdf.metadata_api import _contar_termos

    assert _contar_termos("neurociencias", ("neurociencia", "neurociencias")) == 1
