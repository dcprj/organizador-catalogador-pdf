"""Extração e conversão de PDF para Markdown usando bibliotecas locais."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ErroDeConversao(RuntimeError):
    """Falha ao ler ou converter o PDF."""


@dataclass
class DocumentoConvertido:
    """Resultado da conversão de um PDF."""

    caminho: Path
    markdown_completo: str
    markdown_inicial: str
    total_paginas: int
    metadados_embutidos: dict[str, str] = field(default_factory=dict)

    @property
    def tem_texto(self) -> bool:
        return bool(self.markdown_completo.strip())


def converter_pdf(
    caminho: Path,
    *,
    paginas_para_analise: int = 6,
    max_caracteres_analise: int = 15_000,
) -> DocumentoConvertido:
    """Converte um PDF em Markdown.

    Devolve o Markdown completo (para o arquivo `.md` final) e um recorte das
    primeiras páginas (`markdown_inicial`), que é o único trecho enviado ao LLM
    — é onde ficam capa, folha de rosto e ficha catalográfica, e limitá-lo
    mantém o custo por documento baixo e previsível.
    """
    import pymupdf  # importado sob demanda: carregar o binário custa caro

    try:
        documento = pymupdf.open(caminho)
    except Exception as exc:  # noqa: BLE001 - o PyMuPDF levanta tipos variados
        raise ErroDeConversao(f"não foi possível abrir o PDF: {exc}") from exc

    try:
        if documento.is_encrypted and not documento.authenticate(""):
            raise ErroDeConversao("PDF protegido por senha")

        total_paginas = documento.page_count
        if total_paginas == 0:
            raise ErroDeConversao("PDF sem páginas")

        metadados_embutidos = _metadados_uteis(documento.metadata or {})
        markdown_completo = _para_markdown(documento, paginas=None)

        n = min(paginas_para_analise, total_paginas)
        markdown_inicial = _para_markdown(documento, paginas=list(range(n)))
    finally:
        documento.close()

    if not markdown_completo.strip():
        raise ErroDeConversao(
            "nenhum texto extraível (PDF provavelmente é digitalizado; "
            "seria necessário OCR)"
        )

    return DocumentoConvertido(
        caminho=caminho,
        markdown_completo=markdown_completo,
        markdown_inicial=markdown_inicial[:max_caracteres_analise],
        total_paginas=total_paginas,
        metadados_embutidos=metadados_embutidos,
    )


def _para_markdown(documento, paginas: Optional[list[int]]) -> str:
    """Converte páginas para Markdown, com texto simples como plano B."""
    try:
        import pymupdf4llm

        return pymupdf4llm.to_markdown(documento, pages=paginas, show_progress=False)
    except Exception as exc:  # noqa: BLE001 - qualquer falha cai para o plano B
        logger.debug("pymupdf4llm falhou (%s); usando extração de texto simples", exc)
        indices = paginas if paginas is not None else range(documento.page_count)
        partes = []
        for indice in indices:
            try:
                partes.append(documento[indice].get_text("text"))
            except Exception:  # noqa: BLE001 - páginas corrompidas são ignoradas
                logger.debug("página %d ilegível em %s", indice, documento.name)
        return "\n\n".join(parte for parte in partes if parte)


def _metadados_uteis(brutos: dict) -> dict[str, str]:
    """Filtra os metadados embutidos do PDF que ajudam a extração."""
    interessantes = ("title", "author", "subject", "keywords", "creator", "producer")
    return {
        chave: str(valor).strip()
        for chave, valor in brutos.items()
        if chave in interessantes and valor and str(valor).strip()
    }


def listar_pdfs(origem: Path, *, recursivo: bool = True) -> list[Path]:
    """Lista os PDFs da origem, ordenados, ignorando arquivos ocultos."""
    if not origem.exists():
        raise ErroDeConversao(f"diretório de origem não encontrado: {origem}")
    if not origem.is_dir():
        raise ErroDeConversao(f"a origem não é um diretório: {origem}")

    padrao = "**/*" if recursivo else "*"
    encontrados = [
        caminho
        for caminho in origem.glob(padrao)
        if caminho.is_file()
        and caminho.suffix.lower() == ".pdf"
        and not caminho.name.startswith(".")
    ]
    return sorted(encontrados)
