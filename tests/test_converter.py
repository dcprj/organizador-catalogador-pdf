from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf.converter import (
    ErroDeConversao,
    _combinar_com_orcamento,
    converter_pdf,
)


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


@pytest.fixture
def pdf_com_prefacio_longo(tmp_path: Path) -> Path:
    """9 páginas: as 6 primeiras são prefácio genérico (sem sinal de ficha
    catalográfica); a ficha real só aparece na 7ª página (índice 6),
    fora da janela padrão de 6 páginas."""
    pymupdf = pytest.importorskip("pymupdf")
    caminho = tmp_path / "origem" / "com-prefacio.pdf"
    caminho.parent.mkdir(parents=True, exist_ok=True)

    documento = pymupdf.open()
    for i in range(6):
        pagina = documento.new_page()
        pagina.insert_text((72, 100), f"Prefácio, parte {i + 1}.", fontsize=11)
    pagina_ficha = documento.new_page()
    pagina_ficha.insert_text((72, 100), "Editora Vozes", fontsize=11)
    pagina_ficha.insert_text((72, 120), "ISBN 978-85-326-0871-3", fontsize=11)
    documento.new_page()
    documento.save(caminho)
    documento.close()
    return caminho


class TestBuscaDeFichaCatalografica:
    def test_acha_ficha_alem_da_janela_inicial(self, pdf_com_prefacio_longo: Path):
        documento = converter_pdf(pdf_com_prefacio_longo, paginas_para_analise=6)
        assert "978-85-326-0871-3" in documento.markdown_inicial

    def test_sem_sinal_alem_da_janela_nao_adiciona_nada(self, pdf_de_teste: Path):
        # pdf_de_teste tem só 2 páginas, nenhuma com sinal de ficha
        # catalográfica — o comportamento de sempre (só a janela) se mantém.
        documento = converter_pdf(pdf_de_teste, paginas_para_analise=1)
        assert "Vozes" not in documento.markdown_inicial

    def test_ficha_nao_e_cortada_pelo_teto_de_caracteres(self, tmp_path: Path):
        # Prefácio deliberadamente longo o bastante pra sozinho estourar um
        # teto de caracteres pequeno — sem reserva de orçamento, a ISBN
        # encontrada mais adiante seria cortada fora pelo truncamento final.
        pymupdf = pytest.importorskip("pymupdf")
        caminho = tmp_path / "origem" / "prefacio-longo.pdf"
        caminho.parent.mkdir(parents=True, exist_ok=True)

        documento_pdf = pymupdf.open()
        for _ in range(6):
            pagina = documento_pdf.new_page()
            pagina.insert_text((72, 100), "Lorem ipsum dolor sit amet. " * 50, fontsize=10)
        pagina_ficha = documento_pdf.new_page()
        pagina_ficha.insert_text((72, 100), "ISBN 978-85-326-0871-3", fontsize=11)
        documento_pdf.save(caminho)
        documento_pdf.close()

        documento = converter_pdf(caminho, paginas_para_analise=6, max_caracteres_analise=500)

        assert "978-85-326-0871-3" in documento.markdown_inicial
        assert len(documento.markdown_inicial) <= 500


class TestCombinarComOrcamento:
    def test_sem_texto_extra_so_trunca_o_principal(self):
        assert _combinar_com_orcamento("a" * 100, "", 10) == "a" * 10

    def test_sem_texto_extra_e_dentro_do_teto_nao_mexe(self):
        assert _combinar_com_orcamento("abc", "", 10) == "abc"

    def test_reserva_espaco_para_o_extra_mesmo_com_principal_estourando(self):
        resultado = _combinar_com_orcamento("a" * 1000, "ISBN 123", 30)
        assert "ISBN 123" in resultado
        assert len(resultado) < 1000  # o principal foi de fato cortado
