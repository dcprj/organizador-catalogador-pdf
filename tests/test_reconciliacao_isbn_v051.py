"""Testes offline (mocks HTTP determinísticos) da v0.5.1: CIP tolerante a OCR, ISBN multi-fonte,
cache sem erros transitórios, títulos interrogativos e reconciliação sem falsos positivos de quarentena."""

import json
from unittest.mock import MagicMock, patch

import pytest

from organizador_pdf.classifier_jev import is_invalid_or_disclaimer_title, parse_page_cip
from organizador_pdf.metadata_api import MetadataEnricher, confirmar_mesma_obra
from organizador_pdf.models import (
    ExtractedCandidates,
    Identifiers,
    JevValidationResult,
    PublicationMetadata,
    StatusConsultaProvedor,
)

CIP_FISIOLOGIA = """Direitos exclusivos para a língua portuguesa
Copyright © 2008 by
EDITORA GUANABARA KOOGAN S.A.
Travessa do Ouvidor, 11
Rio de Janeiro, RJ - CEP 20040-040
CIP-BRASIL. CATALOGAÇÃO-NA-FONTE
SINDICATO NACIONAL DOS EDITORES OE LIVROS, RJ.
C858f
Conez., Célia Martins
Fisiologia aplicada à psicologia I Célia Martins Cortez., Dilson Silva. - Rio de
Janeiro : Guanabara Koogan, 2008.
il. ;
ISBN 978-85-277-1383-2
1. Fisiologia humana. 1. Silva, Dílson. II. Titulo.
07-3453.
COO: 612
CDU: 612
"""


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def _enricher(tmp_path, monkeypatch, disk=False):
    monkeypatch.setenv("ORGPDF_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("ORGPDF_CACHE_BIBLIOGRAFICO", raising=False)
    return MetadataEnricher(online=True, enable_disk_cache=disk)


def test_parse_page_cip_ocr_barra_como_I_fisiologia():
    cip = parse_page_cip(CIP_FISIOLOGIA)
    assert cip is not None
    assert cip["authors"] == ["Célia Martins Cortez", "Dilson Silva"]
    assert cip["year"] == 2008
    assert cip["publisher"] == "Guanabara Koogan"
    assert cip["city"] == "Rio de Janeiro"
    assert cip["cdd"] == "612"


def test_brasil_api_multi_provider_complementa_autores_e_ano(tmp_path, monkeypatch):
    enricher = _enricher(tmp_path, monkeypatch)
    isbn = "9788527713832"

    def fake_get(url, *a, **k):
        if "providers=google-books" in url:
            return _resp(200, {"isbn": isbn, "title": "Fisiologia aplicada à psicologia",
                               "authors": ["Célia Martins Cortez", "Dilson Silva"], "year": 2008})
        if "providers=" in url:
            return _resp(404, {})
        return _resp(200, {"isbn": isbn, "title": "Fisiologia aplicada à psicologia",
                           "authors": [], "year": None, "publisher": "Guanabara Koogan"})

    with patch.object(enricher.session, "get", side_effect=fake_get):
        res = enricher.fetch_brasil_api_isbn(isbn)
    assert res["authors"] == ["Célia Martins Cortez", "Dilson Silva"]
    assert res["year"] == 2008
    assert res["publisher"] == "Guanabara Koogan"
    assert enricher.provider_status["brasil_api"] == StatusConsultaProvedor.CONFIRMADO


def test_openlibrary_jscmd_data_extrai_autores(tmp_path, monkeypatch):
    enricher = _enricher(tmp_path, monkeypatch)
    isbn = "9788582714294"
    payload = {
        f"ISBN:{isbn}": {
            "title": "Transtorno de Déficit de Atenção/Hiperatividade",
            "authors": [{"name": "Russell A. Barkley"}],
            "publishers": [{"name": "Artmed"}],
            "publish_places": [{"name": "Porto Alegre"}],
            "publish_date": "March 2020",
            "identifiers": {"isbn_13": [isbn]},
            "subjects": [{"name": "Psicologia"}],
        }
    }
    with patch.object(enricher.session, "get", return_value=_resp(200, payload)):
        res = enricher.fetch_openlibrary_by_isbn(isbn)
    assert res["authors"] == ["Russell A. Barkley"]
    assert res["year"] == 2020
    assert res["publisher"] == "Artmed"
    assert enricher.provider_status["openlibrary"] == StatusConsultaProvedor.CONFIRMADO


def test_disk_cache_nao_persiste_erro_429_mas_memoria_mantem(tmp_path, monkeypatch):
    enricher = _enricher(tmp_path, monkeypatch, disk=True)
    enricher.clear_cache()
    enricher._enable_cache = True
    enricher._set_cache("x:erro", (None, StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE, "HTTP 429"))
    enricher._set_cache("x:ok", ({"title": "ok"}, StatusConsultaProvedor.CONFIRMADO, None))
    assert enricher._cache["x:erro"][1] == StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE  # memória mantém
    salvo = json.loads(enricher._cache_file.read_text(encoding="utf-8"))
    assert "x:erro" not in salvo and "x:ok" in salvo

    # Entradas 429 pré-existentes no JSON são purgadas no carregamento
    salvo["velho:429"] = [None, {"__enum_StatusConsultaProvedor__": "ERRO_HTTP_OU_LIMITE"}, "HTTP 429"]
    enricher._cache_file.write_text(json.dumps(salvo), encoding="utf-8")
    novo = MetadataEnricher(online=True, enable_disk_cache=True)
    assert "velho:429" not in novo._cache
    assert "x:ok" in novo._cache


@pytest.mark.parametrize(
    "titulo",
    [
        "O que é lugar de fala?",
        "O QUE E PSICOLOGIA SOCIAL?",
        "COMO TRABALHA UM PSICANALISTA?",
        "Sentido do cuidado em saúde mental: percepções de usuários e profissionais sobre a atenção "
        "psicossocial no Sistema Único de Saúde (SUS)",
    ],
)
def test_titulos_interrogativos_canonicos_e_subtitulos_longos_aceitos(titulo):
    assert is_invalid_or_disclaimer_title(titulo) is False


def test_pergunta_de_ficha_continua_rejeitada():
    assert is_invalid_or_disclaimer_title("Qual sua interpretação do desenho?") is True
    assert is_invalid_or_disclaimer_title("BACKGROUND AND OBJECTIVES") is True
    assert is_invalid_or_disclaimer_title("Programa de Mestrado em Psicologia - UCDB") is True


def _meta_livro(**kw):
    base = dict(
        title="Fisiologia aplicada à psicologia", authors=["Célia Martins Cortez"], publisher="Guanabara Koogan",
        year=2008, city="Rio de Janeiro", classification="livro", confidence=0.95,
        confidence_classification=0.95, confidence_metadata=0.95,
        identifiers=Identifiers(isbn="9788527713832"), isbn_verified=True, total_pages=300,
    )
    base.update(kw)
    return PublicationMetadata(**base)


def test_divergencia_ano_1_ano_cip_vs_cbl_nao_gera_quarentena(tmp_path, monkeypatch):
    enricher = _enricher(tmp_path, monkeypatch)
    meta = _meta_livro(year=2009, cip_year=2008)
    meta.conflitos_registrados.append("Ano divergente: CIP 2008 vs CBL 2009. Mantido ano explícito do documento.")
    jev = JevValidationResult(
        classification="livro", classification_confidence=0.95,
        candidates=ExtractedCandidates(raw_title=meta.title, isbn="9788527713832"),
    )
    enricher._evaluate_needs_review(meta, jev_result=jev, sources_consulted=["Brasil API / CBL (ISBN)"])
    assert meta.needs_review is False


def test_doi_confirmado_pelo_fn_title_ignora_cabecalho_afiliacao():
    cand = ExtractedCandidates(
        raw_title="Programa de Mestrado em Psicologia",
        raw_authors=["Universidade Católica Dom Bosco"],
        fn_title="Sentido do cuidado em saúde mental e atenção psicossocial no SUS",
        sample_text="texto qualquer",
    )
    api = {
        "title": "Sentido do cuidado em saúde mental: atenção psicossocial no SUS",
        "authors": ["Maria Souza"],
    }
    ok, div = confirmar_mesma_obra(cand, api, "Crossref", is_identifier_match=True)
    assert ok is True and div is None
