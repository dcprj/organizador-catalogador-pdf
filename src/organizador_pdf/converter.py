"""Extração e conversão de PDF para Markdown usando bibliotecas locais."""

from __future__ import annotations

import functools
import io
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
    ocr: bool = True,
    ocr_idioma: str = "por",
) -> DocumentoConvertido:
    """Converte um PDF em Markdown.

    Devolve o Markdown completo (para o arquivo `.md` final) e um recorte das
    primeiras páginas (`markdown_inicial`), que é o único trecho enviado ao LLM
    — é onde ficam capa, folha de rosto e ficha catalográfica, e limitá-lo
    mantém o custo por documento baixo e previsível.

    PDFs sem texto extraível (digitalizados) recorrem ao OCR nativo do
    `pymupdf4llm` (Tesseract por baixo) quando `ocr=True` (padrão) e o
    Tesseract está instalado — sem isso, falham com mensagem explícita,
    como sempre foi. `ocr_idioma` é o idioma do OCR (formato Tesseract, ex.:
    "por", "eng").
    """
    import pymupdf  # importado sob demanda: carregar o binário custa caro

    # O PyMuPDF/pymupdf4llm imprime mensagens de status direto no
    # stdout/stderr por padrão (ex.: "Using Tesseract for OCR processing...",
    # uma vez por página em OCR) — puramente informativo, sem valor pro
    # usuário e sem respeitar o nível de log do app. Descarta.
    pymupdf.set_messages(stream=io.StringIO())

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
        markdown_completo = _para_markdown(
            documento, paginas=None, ocr=ocr, ocr_idioma=ocr_idioma
        )

        n = min(paginas_para_analise, total_paginas)
        markdown_inicial = _para_markdown(
            documento, paginas=list(range(n)), ocr=ocr, ocr_idioma=ocr_idioma
        )
    finally:
        documento.close()

    if not markdown_completo.strip():
        raise _erro_sem_texto(ocr)

    return DocumentoConvertido(
        caminho=caminho,
        markdown_completo=markdown_completo,
        markdown_inicial=markdown_inicial[:max_caracteres_analise],
        total_paginas=total_paginas,
        metadados_embutidos=metadados_embutidos,
    )


@functools.lru_cache(maxsize=1)
def ocr_disponivel() -> bool:
    """Confere se o Tesseract (dados de idioma incluídos) está acessível.

    Cacheado: um lote inteiro faz essa checagem no máximo uma vez, mesmo que
    vários PDFs sem texto precisem gerar a mensagem de erro — evita repetir
    o custo de resolver o caminho do Tesseract a cada arquivo.
    """
    import pymupdf

    try:
        return bool(pymupdf.get_tessdata())
    except Exception as exc:  # noqa: BLE001 - qualquer falha aqui = OCR indisponível
        logger.debug("Tesseract indisponível (%s)", exc)
        return False


def _erro_sem_texto(ocr: bool) -> ErroDeConversao:
    if not ocr:
        return ErroDeConversao(
            "nenhum texto extraível (PDF provavelmente é digitalizado) e o OCR "
            "está desligado (--no-ocr)."
        )
    if not ocr_disponivel():
        return ErroDeConversao(
            "nenhum texto extraível (PDF provavelmente é digitalizado). Instale "
            "o Tesseract para habilitar OCR automático — veja o README."
        )
    return ErroDeConversao(
        "nenhum texto extraível, mesmo com OCR — confira se o PDF não está "
        "corrompido, em branco, ou com qualidade de imagem baixa demais."
    )


def _para_markdown(
    documento, paginas: Optional[list[int]], *, ocr: bool = True, ocr_idioma: str = "por"
) -> str:
    """Converte páginas para Markdown, com texto simples como plano B.

    `ocr`/`ocr_idioma` só têm efeito de fato quando a página não tem texto
    extraível — o `pymupdf4llm` decide por página (`OCRMode.SELECT_KEEP_OLD`)
    se vale a pena rodar OCR nela, e não faz nada com páginas que já têm
    texto. Sem `ocr=True`, OCR nunca é tentado (`OCRMode.NEVER`).
    """
    try:
        import pymupdf4llm
        from pymupdf4llm.ocr import OCRMode

        modo_ocr = OCRMode.SELECT_KEEP_OLD if ocr else OCRMode.NEVER
        return pymupdf4llm.to_markdown(
            documento,
            pages=paginas,
            show_progress=False,
            use_ocr=modo_ocr,
            ocr_language=ocr_idioma,
        )
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
