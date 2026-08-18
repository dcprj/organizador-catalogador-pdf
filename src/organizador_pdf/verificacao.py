"""Verificação de ISBN/DOI contra bases bibliográficas públicas.

Consulta o Crossref (DOI) e a Open Library (ISBN) — ambas gratuitas, sem
chave de API — e compara título/autor devolvidos com o que foi extraído.

Roda só para identificadores já confirmados no texto-fonte (ver
`extractor.descartar_identificadores_nao_confirmados`): a pergunta aqui não é
"esse código existe no PDF?" (isso já foi checado), é "esse código realmente
pertence à obra que extraímos, ou pertence a uma obra diferente?" — o mesmo
tipo de checagem cruzada que pegaria o caso real em que o modelo reconheceu
um autor certo mas anexou um identificador de outra obra.

Não corrige nem sobrescreve metadados — só sinaliza divergências, seguindo o
mesmo princípio do resto do pipeline: incerteza sinalizada é melhor que dado
errado silencioso. Falhas de rede (sem internet, API fora do ar, timeout) são
ignoradas silenciosamente — a verificação é um bônus opcional, o aplicativo
continua funcionando 100% offline sem ela (veja ORGPDF_VERIFICAR_ONLINE).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .models import Metadados

logger = logging.getLogger(__name__)

TIMEOUT = 8.0
USER_AGENT = "organizador-pdf/0.1 (uso pessoal, catalogacao de biblioteca)"

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
OPENLIBRARY_URL = "https://openlibrary.org/api/books"

#: Uma única palavra significativa em comum já é evidência razoável — nomes
#: de autor costumam ser curtos (1-3 palavras).
_MIN_PALAVRAS_EM_COMUM = 1

_PALAVRAS_IRRELEVANTES = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "um", "uma",
    "para", "com", "sem", "em", "no", "na", "nos", "nas", "por", "que",
    "the", "and", "of", "in", "on", "to",
}


def _tokenizar(texto: str) -> set[str]:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    palavras = re.findall(r"[a-zA-Z]{3,}", sem_acento.lower())
    return {p for p in palavras if p not in _PALAVRAS_IRRELEVANTES}


def _bate(a: str, b: str) -> bool:
    """Duas strings "batem" se compartilham ao menos uma palavra significativa.

    Strings vazias não contam contra o match — nada para comparar não é
    evidência de erro (ex.: a API não devolveu autor).
    """
    if not a or not b:
        return True
    return len(_tokenizar(a) & _tokenizar(b)) >= _MIN_PALAVRAS_EM_COMUM


def verificar_identificadores(
    metadados: Metadados, cliente: Optional[httpx.Client] = None
) -> Optional[str]:
    """Confere DOI/ISBN contra Crossref/Open Library; devolve um aviso se divergir.

    Devolve `None` quando os dados batem, quando não há identificador para
    verificar, quando a obra não está indexada nessas bases (cobertura
    incompleta não é evidência de erro) ou quando a verificação falha por
    qualquer motivo de rede.
    """
    fechar = cliente is None
    cliente = cliente or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        doi = metadados.identificadores.doi
        if doi:
            aviso = _verificar_doi(cliente, doi, metadados)
            if aviso:
                return aviso

        isbn = metadados.identificadores.isbn
        if isbn:
            aviso = _verificar_isbn(cliente, isbn, metadados)
            if aviso:
                return aviso
    finally:
        if fechar:
            cliente.close()
    return None


def _normalizar_doi(doi: str) -> str:
    doi = doi.strip()
    for prefixo in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.lower().startswith(prefixo):
            return doi[len(prefixo):]
    return doi


def _verificar_doi(cliente: httpx.Client, doi: str, metadados: Metadados) -> Optional[str]:
    # DOIs podem conter parênteses, `%` e outros caracteres que precisam de
    # escape na URL — preserva a barra literal (separa prefixo/sufixo do DOI).
    doi_escapado = quote(_normalizar_doi(doi), safe="/")
    try:
        resposta = cliente.get(CROSSREF_URL.format(doi=doi_escapado))
    except httpx.HTTPError as exc:
        logger.debug("Verificação de DOI indisponível (%s); seguindo sem ela.", exc)
        return None

    if resposta.status_code != 200:
        # 404 (não indexado) ou qualquer outro erro: não é evidência de nada,
        # só significa que não dá para verificar.
        return None

    try:
        dados = resposta.json().get("message", {})
    except ValueError:
        return None

    titulo_real = " ".join(dados.get("title") or [])
    autores_reais = " ".join(
        f"{a.get('given', '')} {a.get('family', '')}".strip() for a in dados.get("author") or []
    )

    if _bate(metadados.titulo, titulo_real) or _bate(metadados.autor_principal or "", autores_reais):
        return None

    return _mensagem_divergencia("DOI", doi, "Crossref", titulo_real, autores_reais)


def _verificar_isbn(cliente: httpx.Client, isbn: str, metadados: Metadados) -> Optional[str]:
    isbn_limpo = re.sub(r"[\s-]", "", isbn)
    try:
        resposta = cliente.get(
            OPENLIBRARY_URL,
            params={"bibkeys": f"ISBN:{isbn_limpo}", "jscmd": "data", "format": "json"},
        )
    except httpx.HTTPError as exc:
        logger.debug("Verificação de ISBN indisponível (%s); seguindo sem ela.", exc)
        return None

    if resposta.status_code != 200:
        return None

    try:
        corpo: dict[str, Any] = resposta.json()
    except ValueError:
        return None

    dados = corpo.get(f"ISBN:{isbn_limpo}")
    if not dados:
        return None  # não catalogado na Open Library — cobertura incompleta, não é erro

    titulo_real = dados.get("title", "")
    autores_reais = " ".join(a.get("name", "") for a in dados.get("authors") or [])

    if _bate(metadados.titulo, titulo_real) or _bate(metadados.autor_principal or "", autores_reais):
        return None

    return _mensagem_divergencia("ISBN", isbn, "Open Library", titulo_real, autores_reais)


def _mensagem_divergencia(
    tipo: str, valor: str, fonte: str, titulo_real: str, autores_reais: str
) -> str:
    achado = f'"{titulo_real}"' if titulo_real else "uma obra sem título retornado"
    if autores_reais:
        achado += f", de {autores_reais}"
    return (
        f"o {tipo} {valor} está registrado no {fonte} para {achado} — diferente "
        "do que foi extraído. Confira se os metadados são mesmo deste documento."
    )
