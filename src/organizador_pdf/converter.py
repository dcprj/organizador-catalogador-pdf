"""Extração e conversão de PDF para Markdown usando bibliotecas locais."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Sinal de que a página tem ficha catalográfica/identificadores — usado pra
#: achar essa página mesmo quando um prefácio/dedicatória longos a empurram
#: pra fora da janela inicial padrão (`paginas_para_analise`).
_PADRAO_FICHA_CATALOGRAFICA = re.compile(
    r"isbn|issn|\bdoi\b|ficha catalogr[áa]fica|catalogac?[aã]o na publicac?[aã]o|"
    r"dados internacionais de catalogac?[aã]o|cip[\s-]brasil",
    re.IGNORECASE,
)

#: Até onde vale procurar a ficha catalográfica além da janela inicial — a
#: busca é local e barata (texto simples do PyMuPDF, não a conversão
#: estruturada do pymupdf4llm), mas ainda assim limitada pra não custar
#: tempo demais em PDFs muito grandes.
JANELA_BUSCA_FICHA = 25

#: Fração mínima do orçamento de caracteres reservada pra ficha catalográfica
#: encontrada além da janela inicial — sem isso, o truncamento final por
#: `max_caracteres_analise` poderia cortar exatamente o trecho que essa busca
#: existe pra resgatar, se a capa/prefácio já preenchem o teto sozinhos.
_FRACAO_MINIMA_PARA_FICHA = 1 / 3


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

    PDFs sem texto extraível (digitalizados/escaneados) falham com uma
    mensagem explícita — este app não faz OCR. Rode um serviço de OCR externo
    (ex.: ocrmypdf) sobre o arquivo antes de reprocessá-lo.
    """
    import pymupdf  # importado sob demanda: carregar o binário custa caro

    # O PyMuPDF imprime mensagens de status direto no stdout/stderr por
    # padrão — puramente informativo, sem valor pro usuário e sem respeitar
    # o nível de log do app. Descarta.
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
        markdown_completo = _para_markdown(documento, paginas=None)

        n = min(paginas_para_analise, total_paginas)
        markdown_capa = _para_markdown(documento, paginas=list(range(n)))

        pagina_ficha = _procurar_ficha_catalografica(
            documento, a_partir_de=n, total_paginas=total_paginas
        )
        if pagina_ficha is None:
            markdown_inicial = markdown_capa
        else:
            paginas_ficha = [pagina_ficha]
            if pagina_ficha + 1 < total_paginas:
                paginas_ficha.append(pagina_ficha + 1)
            markdown_ficha = _para_markdown(documento, paginas=paginas_ficha)
            markdown_inicial = _combinar_com_orcamento(
                markdown_capa, markdown_ficha, max_caracteres_analise
            )
    finally:
        documento.close()

    if not markdown_completo.strip():
        raise ErroDeConversao(
            "nenhum texto extraível — o PDF provavelmente é digitalizado/"
            "escaneado. Este app não faz OCR: rode um serviço externo (ex.: "
            "ocrmypdf) sobre o arquivo antes de reprocessá-lo."
        )

    return DocumentoConvertido(
        caminho=caminho,
        markdown_completo=markdown_completo,
        markdown_inicial=markdown_inicial[:max_caracteres_analise],
        total_paginas=total_paginas,
        metadados_embutidos=metadados_embutidos,
    )


def _procurar_ficha_catalografica(
    documento, *, a_partir_de: int, total_paginas: int
) -> Optional[int]:
    """Acha a 1ª página, além da janela inicial, com sinal de ficha
    catalográfica (ISBN/ISSN/DOI/"ficha catalográfica"/etc.).

    Cobre o caso de um prefácio, dedicatória ou sumário longos empurrarem
    essa página pra fora das N primeiras páginas enviadas por padrão. Usa
    texto simples do PyMuPDF (rápido, sem custo de LLM) — só decide *quais*
    páginas valem a pena mandar pro modelo, não substitui a conversão.
    """
    limite = min(total_paginas, JANELA_BUSCA_FICHA)
    for indice in range(a_partir_de, limite):
        try:
            texto = documento[indice].get_text("text")
        except Exception:  # noqa: BLE001 - página ilegível não trava a busca
            logger.debug("página %d ilegível ao procurar ficha catalográfica", indice)
            continue
        if _PADRAO_FICHA_CATALOGRAFICA.search(texto):
            return indice
    return None


def _combinar_com_orcamento(texto_principal: str, texto_extra: str, teto: int) -> str:
    """Junta capa + ficha catalográfica reservando espaço mínimo pra 2ª.

    Sem isso, o truncamento final por `max_caracteres_analise` cortaria a
    string pela frente — se a capa/prefácio sozinhos já preenchem o teto, a
    ficha catalográfica encontrada mais adiante nunca chegaria a aparecer,
    justamente o caso que essa busca existe pra resgatar.
    """
    if not texto_extra.strip():
        return texto_principal[:teto]
    orcamento_extra = min(len(texto_extra), max(int(teto * _FRACAO_MINIMA_PARA_FICHA), 1))
    orcamento_principal = max(teto - orcamento_extra, 0)
    return (
        f"{texto_principal[:orcamento_principal].rstrip()}\n\n"
        f"[...]\n\n{texto_extra[:orcamento_extra]}"
    )


def _para_markdown(documento, paginas: Optional[list[int]]) -> str:
    """Converte páginas para Markdown, com texto simples como plano B.

    `use_ocr=OCRMode.NEVER` é explícito de propósito: sem ele, o padrão do
    `pymupdf4llm` (`SELECT_KEEP_OLD`) roda OCR automaticamente quando julga
    valer a pena e o Tesseract estiver instalado na máquina — o que
    contradiz a decisão de nunca fazer OCR neste app.
    """
    try:
        import pymupdf4llm
        from pymupdf4llm.ocr import OCRMode

        return pymupdf4llm.to_markdown(
            documento, pages=paginas, show_progress=False, use_ocr=OCRMode.NEVER
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
