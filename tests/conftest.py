from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao


@pytest.fixture
def metadados() -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Psicologia",
        subarea="Logoterapia",
        titulo="Em Busca de Sentido",
        subtitulo="Um psicólogo no campo de concentração",
        # Formato bibliográfico "Sobrenome, Nome" — o mesmo usado dentro de
        # referencia_abnt, só sem o SOBRENOME em maiúsculas (regra só da
        # referência formatada, não do campo de metadados).
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
    """Gera um PDF real, com texto extraível, para os testes de ponta a ponta."""
    pymupdf = pytest.importorskip("pymupdf")

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
