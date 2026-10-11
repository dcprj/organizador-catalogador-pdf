"""Shared Pytest fixtures and mock utilities."""

from __future__ import annotations

from pathlib import Path
import pytest
import pymupdf

from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao


@pytest.fixture
def sample_pdf_generator(tmp_path: Path):
    """Factory fixture to generate synthetic PDFs with selectable native text."""
    def _create_pdf(
        filename: str,
        title: str,
        text_pages: list,
        metadata: dict = None,
    ) -> Path:
        doc = pymupdf.open()
        for idx, page_content in enumerate(text_pages, start=1):
            page = doc.new_page()
            rect = pymupdf.Rect(50, 50, 550, 750)
            page.insert_textbox(rect, page_content, fontsize=12)

        if metadata:
            doc.set_metadata(metadata)

        pdf_path = tmp_path / filename
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    return _create_pdf


@pytest.fixture
def metadados() -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Psicologia",
        subarea="Logoterapia",
        titulo="Em Busca de Sentido",
        subtitulo="Um psicólogo no campo de concentração",
        autores=["Frankl, Viktor E."],
        autor_principal="Frankl, Viktor E.",
        editora_ou_periodico="Vozes",
        ano=2019,
        local="Petrópolis",
        identificadores=Identificadores(isbn="978-85-326-0871-3"),
        referencia_abnt=(
            "FRANKL, Viktor E. Em busca de sentido: um psicólogo no campo de "
            "concentração. 49. ed. Petrópolis: Vozes, 2019."
        ),
    )


@pytest.fixture
def pdf_de_teste(tmp_path: Path) -> Path:
    """Gera um PDF real, com texto extraível, para testes."""
    caminho = tmp_path / "origem" / "documento.pdf"
    caminho.parent.mkdir(parents=True, exist_ok=True)

    documento = pymupdf.open()
    pagina = documento.new_page()
    pagina.insert_text((72, 100), "Em Busca de Sentido", fontsize=20)
    pagina.insert_text((72, 130), "Viktor E. Frankl", fontsize=12)
    segunda = documento.new_page()
    segunda.insert_text((72, 100), "Editora Vozes, Petropolis, 2019.", fontsize=11)
    documento.save(caminho)
    documento.close()

    return caminho
