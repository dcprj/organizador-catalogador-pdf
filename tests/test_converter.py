from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf.converter import ErroDeConversao, converter_pdf


@pytest.fixture
def pdf_digitalizado(tmp_path: Path) -> Path:
    """PDF com página em branco — sem nenhum texto extraível, como um PDF
    digitalizado sem camada de texto."""
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

    def test_pdf_sem_texto_falha_com_mensagem_explicita(self, pdf_digitalizado: Path):
        # Este app não faz OCR — o erro precisa orientar o usuário a resolver
        # isso fora (serviço externo), não parecer um bug do app.
        with pytest.raises(ErroDeConversao, match="não faz OCR"):
            converter_pdf(pdf_digitalizado)

    def test_nunca_aciona_ocr_automatico_da_biblioteca(
        self, pdf_digitalizado: Path, monkeypatch
    ):
        """`pymupdf4llm` roda OCR sozinho por padrão (`OCRMode.SELECT_KEEP_OLD`)
        quando o Tesseract está instalado na máquina — o app precisa desligar
        isso explicitamente (`OCRMode.NEVER`), senão o comportamento depende
        do que estiver instalado no ambiente de quem roda."""
        from pymupdf4llm.ocr import OCRMode

        chamadas = []

        def falso_to_markdown(documento, *, pages, show_progress, use_ocr):
            chamadas.append(use_ocr)
            return ""

        monkeypatch.setattr("pymupdf4llm.to_markdown", falso_to_markdown)

        with pytest.raises(ErroDeConversao):
            converter_pdf(pdf_digitalizado)

        assert chamadas and all(modo == OCRMode.NEVER for modo in chamadas)
