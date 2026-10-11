"""Testes de conformidade para o fluxo rastreável de identificação, classificação e organização de PDFs.

Cobre os 8 passos do fluxo obrigatório:
1. Extração de texto e avaliação de suficiência (OCR / revisão manual)
2. Extração de candidatos a metadados com preservação de originais e confiança
3. Classificação de tipo e assunto com o JEV (justificativa e área/subárea)
4. Pesquisa de metadados nas fontes externas sem assinatura (DOAJ, PubMed, Europe PMC, ERIC, SciELO, etc.)
5. Confirmação estrita de mesma obra (não aceitar só por título parecido)
6. Reconciliação campo a campo com proveniência e ISBN estritamente separado da ABNT
7. Avaliação de qualidade do resultado e critérios atendidos
8. Encaminhamento e geração do arquivo <stem>.auditoria.json
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymupdf
import pytest

from organizador_pdf.abnt_formatter import ABNTFormatter
from organizador_pdf.classifier_jev import JevClassifier, extract_candidate_metadata
from organizador_pdf.config import Config
from organizador_pdf.converter import converter_pdf
from organizador_pdf.metadata_api import (
    MetadataEnricher,
    confirmar_mesma_obra,
)
from organizador_pdf.models import (
    CandidatoMetadado,
    ExtractedCandidates,
    Identificadores,
    JevValidationResult,
    Metadados,
    PublicationType,
    ResultadoExtracaoTexto,
    StatusConsultaProvedor,
    TipoPublicacao,
)
from organizador_pdf.organizer import organizar
from organizador_pdf.pipeline import OpcoesDoPipeline, Pipeline, Situacao


# ==============================================================================
# Passo 1: Leitura do PDF, extração de texto nativo e suficiência
# ==============================================================================
class TestPasso1ExtracaoESuficiencia:
    def test_texto_nativo_suficiente(self, tmp_path: Path):
        pdf_path = tmp_path / "artigo_completo.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text(
            (72, 100),
            "Efeitos da Inteligência Artificial na Educação Superior Brasileira.\n"
            "Autor: Maria Silva Santos, Universidade de São Paulo, 2024.\n"
            "Resumo: Este trabalho analisa a aplicação de modelos de linguagem em salas de aula universitárias.",
            fontsize=12,
        )
        doc.save(str(pdf_path))
        doc.close()

        doc_conv = converter_pdf(pdf_path)
        assert doc_conv.extracao_texto is not None
        assert doc_conv.extracao_texto.suficiente is True
        assert doc_conv.extracao_texto.requer_ocr is False
        assert doc_conv.extracao_texto.motivo_insuficiencia is None
        assert doc_conv.extracao_texto.total_palavras >= 15

    def test_texto_nativo_insuficiente_encaminha_para_revisao(self, tmp_path: Path):
        pdf_path = tmp_path / "texto_escasso.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        # Apenas 23 caracteres úteis e 3 palavras (insuficiente para identificar a obra)
        page.insert_text((72, 100), "Página 1 rascunho", fontsize=10)
        doc.save(str(pdf_path))
        doc.close()

        doc_conv = converter_pdf(pdf_path)
        assert doc_conv.extracao_texto is not None
        assert doc_conv.extracao_texto.suficiente is False
        assert doc_conv.extracao_texto.requer_ocr is True
        assert "insuficiente para identificação segura" in doc_conv.extracao_texto.motivo_insuficiencia


# ==============================================================================
# Passo 2: Extrair candidatos a metadados preservando originais e confiança
# ==============================================================================
class TestPasso2CandidatosEMetadados:
    def test_preserva_candidatos_originais_e_detalhados(self, tmp_path: Path):
        pdf_path = tmp_path / "livro_cip.pdf"
        cip_texto = (
            "Ficha catalográfica\n"
            "Silva, João da\n"
            "Fundamentos de Física Moderna / João da Silva. -- 2. ed. -- São Paulo : Editora Ciência, 2021.\n"
            "ISBN 978-85-1234-567-8\n"
            "1. Física 2. Mecânica Quântica I. Título."
        )
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), cip_texto, fontsize=10)
        doc.save(str(pdf_path))
        doc.close()

        candidates = extract_candidate_metadata(
            cip_texto,
            pdf_path=str(pdf_path),
            total_pages=1,
            reabrir_pdf=True,
        )
        assert candidates.raw_title == "Fundamentos de Física Moderna"
        assert candidates.isbn == "9788512345678"
        assert len(candidates.candidatos_detalhados) > 0
        assert "titulo" in candidates.candidatos_detalhados
        assert "isbn" in candidates.candidatos_detalhados
        assert candidates.candidatos_detalhados["titulo"].origem == "ficha_catalografica"
        assert candidates.candidatos_detalhados["titulo"].confianca >= 0.95
        assert len(candidates.candidatos_originais) > 0
        assert candidates.candidatos_originais.get("isbn") == "9788512345678"


# ==============================================================================
# Passo 3: Classificar tipo e assunto com o JEV
# ==============================================================================
class TestPasso3ClassificacaoJEV:
    def test_classificacao_tipo_assunto_e_justificativa(self):
        classifier = JevClassifier(modo="local")
        candidates = ExtractedCandidates(
            raw_title="Estudo sobre Polímeros e Nanotecnologia",
            raw_authors=["Almeida, Carlos"],
            raw_area="Química",
            sample_text="Este artigo apresenta uma análise de polímeros avançados para engenharia.",
            total_pages=12,
        )
        res = classifier._run_calibrated_fallback(candidates.sample_text, candidates, total_pages=12)
        assert res.classification in ("artigo", "livro", "outros")
        assert res.assunto != ""
        assert res.subarea != ""
        assert len(res.justificativa) > 0
        assert "Classificado como" in res.justificativa
        assert "Química" in res.justificativa


# ==============================================================================
# Passo 4: Pesquisar metadados nas fontes externas sem assinatura
# ==============================================================================
class TestPasso4FontesExternas:
    def test_doaj_busca_por_doi(self):
        enricher = MetadataEnricher(online=True)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "bibjson": {
                        "title": "Open Access in Science",
                        "journal": {"title": "Journal of Open Science"},
                        "year": "2023",
                        "author": [{"name": "Smith, John"}],
                        "link": [{"type": "fulltext", "url": "https://example.com/fulltext.pdf"}],
                    }
                }
            ]
        }
        with patch.object(enricher.session, "get", return_value=mock_response):
            result = enricher.fetch_doaj_by_doi("10.1234/doaj.test")
            assert result is not None
            assert result["title"] == "Open Access in Science"
            assert result["url_texto_integral"] == "https://example.com/fulltext.pdf"
            assert len(enricher.consultas_realizadas) > 0
            consulta = enricher.consultas_realizadas[-1]
            assert consulta.fonte == "DOAJ"
            assert consulta.status == StatusConsultaProvedor.CONFIRMADO
            assert consulta.tem_texto_integral is True

    def test_pubmed_busca_por_doi(self):
        enricher = MetadataEnricher(online=True)
        # 1. esearch response
        mock_esearch = MagicMock()
        mock_esearch.status_code = 200
        mock_esearch.json.return_value = {"esearchresult": {"idlist": ["12345678"]}}
        # 2. esummary response
        mock_esummary = MagicMock()
        mock_esummary.status_code = 200
        mock_esummary.json.return_value = {
            "result": {
                "12345678": {
                    "title": "Clinical trials in oncology",
                    "source": "Journal of Medicine",
                    "pubdate": "2022 Jan",
                    "authors": [{"name": "Johnson A"}],
                }
            }
        }
        with patch.object(enricher.session, "get", side_effect=[mock_esearch, mock_esummary]):
            result = enricher.fetch_pubmed_by_doi("10.1016/j.med.2022")
            assert result is not None
            assert result["title"] == "Clinical trials in oncology"
            assert result["source"] == "api_pubmed"
            assert len(enricher.consultas_realizadas) > 0
            consulta = enricher.consultas_realizadas[-1]
            assert consulta.fonte == "PubMed"
            assert consulta.status == StatusConsultaProvedor.CONFIRMADO

    def test_europe_pmc_busca_por_doi(self):
        enricher = MetadataEnricher(online=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "resultList": {
                "result": [
                    {
                        "title": "Genomic variations in human disease",
                        "journalTitle": "Nature Genetics",
                        "pubYear": "2021",
                        "authorString": "Lee C, Brown D",
                        "isOpenAccess": "Y",
                        "pmcid": "PMC123456",
                    }
                ]
            }
        }
        with patch.object(enricher.session, "get", return_value=mock_resp):
            result = enricher.fetch_europe_pmc_by_doi("10.1038/ng.1234")
            assert result is not None
            assert result["title"] == "Genomic variations in human disease"
            assert result["url_texto_integral"] == "https://europepmc.org/articles/PMC123456?pdf=render"
            consulta = enricher.consultas_realizadas[-1]
            assert consulta.fonte == "Europe PMC"
            assert consulta.tem_texto_integral is True

    def test_eric_busca_por_titulo(self):
        enricher = MetadataEnricher(online=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": {
                "docs": [
                    {
                        "id": "ED123456",
                        "title": "Teaching Methods in STEM",
                        "author": ["Taylor, Robert"],
                        "source": "Education Review",
                        "publicationdateyear": 2020,
                        "description": "Evaluation of modern STEM teaching methodologies.",
                    }
                ]
            }
        }
        with patch.object(enricher.session, "get", return_value=mock_resp):
            result = enricher.fetch_eric_by_title("Teaching Methods in STEM")
            assert result is not None
            assert result["title"] == "Teaching Methods in STEM"
            assert result["source"] == "api_eric"
            consulta = enricher.consultas_realizadas[-1]
            assert consulta.fonte == "ERIC"
            assert consulta.status == StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO

    def test_google_scholar_sem_chave_registra_status_correto(self):
        enricher = MetadataEnricher(online=True)
        result = enricher.fetch_google_scholar("Titulo Qualquer")
        assert result is None
        consulta = enricher.consultas_realizadas[-1]
        assert consulta.fonte == "Google Acadêmico"
        assert consulta.status == StatusConsultaProvedor.REQUER_AUTENTICACAO_OU_CHAVE
        assert "SERPAPI_API_KEY" in (consulta.erro_ou_aviso or "") or "SerpApi" in (consulta.erro_ou_aviso or "")


# ==============================================================================
# Passo 5: Confirmar que o resultado corresponde à mesma obra (confirmação estrita)
# ==============================================================================
class TestPasso5ConfirmacaoEstrita:
    def test_rejeita_apenas_por_titulo_parecido_se_autores_divergirem(self):
        # Título tem semelhança léxica mas autores e obras são totalmente diferentes
        local = ExtractedCandidates(
            raw_title="Introdução à Economia Monetária",
            raw_authors=["Carvalho, Fernando"],
            raw_year=2015,
        )
        externo = {
            "title": "Introdução à Economia Internacional e Monetária",
            "authors": ["Silva, João"],
            "year": 2020,
        }
        corresponde, _ = confirmar_mesma_obra(local, externo, provider_name="DOAJ", is_identifier_match=False)
        assert corresponde is False

    def test_aceita_quando_doi_bate(self):
        local = ExtractedCandidates(
            raw_title="Título no PDF",
            doi="10.1234/test.2021",
        )
        externo = {
            "title": "Título no Crossref Oficial",
            "doi": "10.1234/test.2021",
        }
        corresponde, _ = confirmar_mesma_obra(local, externo, provider_name="Crossref", is_identifier_match=True)
        assert corresponde is True

    def test_tolera_cabecalho_de_periodico_se_autor_bate(self):
        # Muito comum em artigos acadêmicos: PDF extrai nome do periódico no cabeçalho
        local = ExtractedCandidates(
            raw_title="Revista Brasileira de Ensino de Física vol. 42",
            raw_authors=["Oliveira, Marcos"],
            doi="10.1590/1806-9126-rbef-2020-001",
        )
        externo = {
            "title": "Estudo do Movimento Harmônico com Sensores",
            "authors": ["Oliveira, Marcos"],
            "journal": "Revista Brasileira de Ensino de Física",
            "doi": "10.1590/1806-9126-rbef-2020-001",
        }
        corresponde, _ = confirmar_mesma_obra(local, externo, provider_name="SciELO", is_identifier_match=True)
        assert corresponde is True


# ==============================================================================
# Passo 6: Reconciliação campo a campo e preservação do ISBN fora da ABNT
# ==============================================================================
class TestPasso6ReconciliacaoEIsbn:
    def test_isbn_preservado_em_campo_proprio_fora_da_referencia_abnt(self):
        meta = Metadados(
            tipo_publicacao=TipoPublicacao.LIVRO,
            area_principal="Ciências Exatas",
            subarea="Física",
            titulo="Física Quântica",
            autores=["Eisberg, Robert", "Resnick, Robert"],
            autor_principal="Eisberg, Robert",
            editora_ou_periodico="Campus",
            local="Rio de Janeiro",
            ano=1979,
            identificadores=Identificadores(isbn="978-85-7001-012-3"),
        )
        ref_abnt = ABNTFormatter.format(meta)
        # O ISBN NÃO deve estar presente na citação ABNT NBR 6023
        assert "978-85-7001-012-3" not in ref_abnt
        assert "9788570010123" not in ref_abnt
        assert "ISBN" not in ref_abnt
        # Mas está preservado no campo próprio
        assert meta.identificadores.isbn == "978-85-7001-012-3"

    def test_reconciliacao_campos_registra_origem_e_evidencia(self):
        enricher = MetadataEnricher(online=False)
        candidates = ExtractedCandidates(
            raw_title="Título Local",
            raw_authors=["Autor Local"],
            isbn="978-85-1234-567-8",
            isbn_source="ficha_catalografica",
            title_source="primeira_pagina",
            author_source="primeira_pagina",
        )
        jev_res = JevValidationResult(
            classification="livro",
            assunto="Filosofia",
            subarea="Ética",
            justificativa="Estrutura de livro monográfico.",
        )
        jev_res.candidates = candidates

        meta = enricher.enrich(jev_res)
        assert len(meta.reconciliacao_campos) > 0
        assert "titulo" in meta.reconciliacao_campos
        assert meta.reconciliacao_campos["titulo"]["fonte"] == "primeira_pagina"
        assert meta.reconciliacao_campos["titulo"]["valor_escolhido"] == "Título Local"
        assert "isbn" in meta.reconciliacao_campos
        assert meta.reconciliacao_campos["isbn"]["fonte"] == "ficha_catalografica"


# ==============================================================================
# Passo 7: Avaliação da qualidade do resultado e critérios atendidos
# ==============================================================================
class TestPasso7QualidadeECriterios:
    def test_criterios_atendidos_preenchidos(self):
        enricher = MetadataEnricher(online=False)
        candidates = ExtractedCandidates(
            raw_title="Engenharia de Software",
            raw_authors=["Pressman, Roger"],
            isbn="978-85-8055-044-3",
            isbn_source="ficha_catalografica",
            raw_publisher="McGraw-Hill",
            raw_year=2016,
        )
        jev_res = JevValidationResult(
            classification="livro",
            classification_confidence=0.9,
            assunto="Ciência da Computação",
            subarea="Engenharia de Software",
            justificativa="Classificação compatível com estrutura de livro texto.",
        )
        jev_res.candidates = candidates

        meta = enricher.enrich(jev_res)
        assert len(meta.criterios_atendidos) > 0
        assert "compatibilidade_jev_tipo" in meta.criterios_atendidos
        assert "compatibilidade_jev_assunto" in meta.criterios_atendidos
        assert "identificador_presente" in meta.criterios_atendidos


# ==============================================================================
# Passo 8: Organização e geração do arquivo <stem>.auditoria.json
# ==============================================================================
class TestPasso8AuditoriaEOrganizacao:
    def test_organizar_grava_arquivo_auditoria_json(self, tmp_path: Path):
        origem = tmp_path / "origem" / "artigo_teste.pdf"
        origem.parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        p = doc.new_page()
        p.insert_text((72, 100), "Artigo Acadêmico Teste de Auditoria Completo", fontsize=14)
        p.insert_text((72, 130), "Autor: João Carlos da Silva", fontsize=12)
        p.insert_text((72, 160), "Universidade Federal de Minas Gerais, Belo Horizonte, 2023.", fontsize=11)
        p.insert_text((72, 190), "Resumo: Este trabalho apresenta uma análise detalhada dos registros de auditoria.", fontsize=10)
        doc.save(str(origem))
        doc.close()

        destino = tmp_path / "destino"
        pipeline = Pipeline(
            Config(verificar_online=False),
            OpcoesDoPipeline(destino=destino, mover=False),
        )

        res = pipeline.processar_arquivo(origem)
        assert res.situacao is Situacao.SUCESSO
        assert res.pdf_destino.exists()

        # Verifica se o arquivo de auditoria foi gravado junto ao PDF organizado
        auditoria_file = res.pdf_destino.parent / f"{res.pdf_destino.stem}.auditoria.json"
        assert auditoria_file.exists()

        dados_auditoria = json.loads(auditoria_file.read_text(encoding="utf-8"))
        assert "identificador_arquivo" in dados_auditoria
        assert len(dados_auditoria["identificador_arquivo"]) == 64  # SHA-256
        assert dados_auditoria["nome_arquivo"] == "artigo_teste.pdf"
        assert "decisao_encaminhamento" in dados_auditoria
        assert dados_auditoria["decisao_encaminhamento"] == "organizado"
        assert "justificativa_decisao" in dados_auditoria
        assert "candidatos_metadados" in dados_auditoria
        assert "candidatos_originais_preservados" in dados_auditoria
        assert "resposta_jev" in dados_auditoria
        assert "fontes_consultadas" in dados_auditoria
        assert "valores_finais_selecionados" in dados_auditoria
        assert "criterios_confianca_atendidos" in dados_auditoria
