from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf.converter import ErroDeConversao, converter_pdf, ocr_disponivel


@pytest.fixture(autouse=True)
def limpar_cache_ocr_disponivel():
    """`ocr_disponivel()` é cacheada (lru_cache) para não invocar o Tesseract
    repetidamente num lote real — em teste isso vazaria entre casos que
    monkeypatcham resultados diferentes, então limpamos antes de cada um."""
    ocr_disponivel.cache_clear()
    yield
    ocr_disponivel.cache_clear()


@pytest.fixture
def pdf_digitalizado(tmp_path: Path) -> Path:
    """PDF com página em branco — sem nenhum texto extraível, como um PDF
    digitalizado sem camada de texto (o que importa pro conversor é que a
    extração normal devolva vazio; o OCR em si é sempre mockado nos testes,
    nunca roda de verdade aqui)."""
    pymupdf = pytest.importorskip("pymupdf")

    caminho = tmp_path / "origem" / "escaneado.pdf"
    caminho.parent.mkdir(parents=True, exist_ok=True)

    documento = pymupdf.open()
    documento.new_page()
    documento.save(caminho)
    documento.close()

    return caminho


class TestConverterPdf:
    def test_extrai_texto_e_limita_o_trecho_de_analise(self, pdf_de_teste: Path):
        documento = converter_pdf(
            pdf_de_teste, paginas_para_analise=1, max_caracteres_analise=50
        )

        assert documento.total_paginas == 2
        assert "Em Busca de Sentido" in documento.markdown_completo
        assert "Vozes" in documento.markdown_completo  # veio da 2ª página
        assert len(documento.markdown_inicial) <= 50
        assert "Vozes" not in documento.markdown_inicial  # análise só da 1ª página


class TestOcrDisponivel:
    def test_true_quando_tessdata_encontrado(self, monkeypatch):
        monkeypatch.setattr(
            "pymupdf.get_tessdata", lambda: "/opt/homebrew/share/tessdata/", raising=False
        )
        assert ocr_disponivel() is True

    def test_false_quando_tessdata_ausente(self, monkeypatch):
        def levanta():
            raise RuntimeError("tessdata não encontrado")

        monkeypatch.setattr("pymupdf.get_tessdata", levanta, raising=False)
        assert ocr_disponivel() is False

    def test_resultado_e_cacheado(self, monkeypatch):
        chamadas = 0

        def contar():
            nonlocal chamadas
            chamadas += 1
            return "/opt/homebrew/share/tessdata/"

        monkeypatch.setattr("pymupdf.get_tessdata", contar, raising=False)
        ocr_disponivel()
        ocr_disponivel()
        assert chamadas == 1


class TestOcrEmConverterPdf:
    """`pymupdf4llm.to_markdown` é sempre mockado aqui — o OCR de verdade
    (engine Tesseract) já é responsabilidade do próprio pymupdf4llm; o que
    este módulo precisa garantir é que passa os parâmetros certos pra ele e
    trata o resultado (vazio ou não) corretamente."""

    def test_ocr_ligado_usa_select_keep_old_e_idioma_configurado(
        self, pdf_digitalizado: Path, monkeypatch
    ):
        from pymupdf4llm.ocr import OCRMode

        chamadas = []

        def falso_to_markdown(documento, *, pages, show_progress, use_ocr, ocr_language):
            chamadas.append((use_ocr, ocr_language))
            return "Texto reconhecido via OCR"

        monkeypatch.setattr("pymupdf4llm.to_markdown", falso_to_markdown)

        documento = converter_pdf(pdf_digitalizado, ocr_idioma="por")

        assert "Texto reconhecido via OCR" in documento.markdown_completo
        assert chamadas and all(
            modo == OCRMode.SELECT_KEEP_OLD and idioma == "por" for modo, idioma in chamadas
        )

    def test_ocr_desligado_usa_modo_never(self, pdf_digitalizado: Path, monkeypatch):
        from pymupdf4llm.ocr import OCRMode

        chamadas = []

        def falso_to_markdown(documento, *, pages, show_progress, use_ocr, ocr_language):
            chamadas.append(use_ocr)
            return ""

        monkeypatch.setattr("pymupdf4llm.to_markdown", falso_to_markdown)
        monkeypatch.setattr("organizador_pdf.converter.ocr_disponivel", lambda: True)

        with pytest.raises(ErroDeConversao, match="desligado"):
            converter_pdf(pdf_digitalizado, ocr=False)

        assert chamadas and all(modo == OCRMode.NEVER for modo in chamadas)

    def test_sem_tesseract_erro_orienta_instalar(self, pdf_digitalizado: Path, monkeypatch):
        monkeypatch.setattr("pymupdf4llm.to_markdown", lambda *a, **k: "")
        monkeypatch.setattr("organizador_pdf.converter.ocr_disponivel", lambda: False)

        with pytest.raises(ErroDeConversao, match="Instale o Tesseract"):
            converter_pdf(pdf_digitalizado)

    def test_com_tesseract_mas_ainda_sem_texto_erro_generico(
        self, pdf_digitalizado: Path, monkeypatch
    ):
        monkeypatch.setattr("pymupdf4llm.to_markdown", lambda *a, **k: "")
        monkeypatch.setattr("organizador_pdf.converter.ocr_disponivel", lambda: True)

        with pytest.raises(ErroDeConversao, match="mesmo com OCR"):
            converter_pdf(pdf_digitalizado)

    def test_ocr_desligado_nao_verifica_tesseract_disponivel(
        self, pdf_digitalizado: Path, monkeypatch
    ):
        # Com --no-ocr a mensagem já é outra (menciona a flag) — não faz
        # sentido gastar a checagem de Tesseract disponível nesse caminho.
        chamou = False

        def marcar():
            nonlocal chamou
            chamou = True
            return False

        monkeypatch.setattr("pymupdf4llm.to_markdown", lambda *a, **k: "")
        monkeypatch.setattr("organizador_pdf.converter.ocr_disponivel", marcar)

        with pytest.raises(ErroDeConversao, match="desligado"):
            converter_pdf(pdf_digitalizado, ocr=False)

        assert chamou is False

    def test_pdf_com_texto_normal_nao_verifica_ocr_disponivel(
        self, pdf_de_teste: Path, monkeypatch
    ):
        chamou = False

        def marcar():
            nonlocal chamou
            chamou = True
            return True

        monkeypatch.setattr("organizador_pdf.converter.ocr_disponivel", marcar)

        converter_pdf(pdf_de_teste)

        assert chamou is False
