"""Verificação de ISBN/DOI contra bases bibliográficas públicas.

Consulta o Crossref (DOI) e a Open Library (ISBN) — ambas gratuitas, sem
chave de API — e compara título/autor devolvidos com o que foi extraído.

Roda só para identificadores já confirmados no texto-fonte (ver
`extractor.descartar_identificadores_nao_confirmados`): a pergunta aqui não é
"esse código existe no PDF?" (isso já foi checado), é "esse código realmente
pertence à obra que extraímos, ou pertence a uma obra diferente?" — o mesmo
tipo de checagem cruzada que pegaria o caso real em que o modelo reconheceu
um autor certo mas anexou um identificador de outra obra.

Nunca sobrescreve um campo já extraído — só sinaliza divergência, seguindo o
mesmo princípio do resto do pipeline: incerteza sinalizada é melhor que dado
errado silencioso. Mas quando os dados BATEM (confirmando que o identificador
é da mesma obra) e a API tem informação que o LLM não extraiu (editora, ano,
local), essas lacunas são preenchidas — diferente de corrigir um valor já
afirmado, completar um `null` a partir de uma base curada e já confirmada
como a mesma obra é estritamente menos arriscado que confiar no palpite do
LLM. Falhas de rede (sem internet, API fora do ar, timeout) são ignoradas
silenciosamente — a verificação é um bônus opcional, o aplicativo continua
funcionando 100% offline sem ela (veja ORGPDF_VERIFICAR_ONLINE).
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
) -> tuple[Metadados, Optional[str]]:
    """Confere DOI/ISBN contra Crossref/Open Library.

    Devolve `(metadados, aviso)`. `aviso` é `None` quando os dados batem,
    quando não há identificador para verificar, quando a obra não está
    indexada nessas bases (cobertura incompleta não é evidência de erro) ou
    quando a verificação falha por qualquer motivo de rede. Quando os dados
    batem, `metadados` pode vir com `editora_ou_periodico`/`ano`/`local`
    preenchidos a partir da API, se estavam nulos (nunca sobrescreve valor
    já existente).
    """
    fechar = cliente is None
    cliente = cliente or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        doi = metadados.identificadores.doi
        if doi:
            metadados, aviso = _verificar_doi(cliente, doi, metadados)
            if aviso:
                return metadados, aviso

        isbn = metadados.identificadores.isbn
        if isbn:
            metadados, aviso = _verificar_isbn(cliente, isbn, metadados)
            if aviso:
                return metadados, aviso
    finally:
        if fechar:
            cliente.close()
    return metadados, None


def _normalizar_doi(doi: str) -> str:
    doi = doi.strip()
    for prefixo in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.lower().startswith(prefixo):
            return doi[len(prefixo):]
    return doi


def _verificar_doi(
    cliente: httpx.Client, doi: str, metadados: Metadados
) -> tuple[Metadados, Optional[str]]:
    # DOIs podem conter parênteses, `%` e outros caracteres que precisam de
    # escape na URL — preserva a barra literal (separa prefixo/sufixo do DOI).
    doi_escapado = quote(_normalizar_doi(doi), safe="/")
    try:
        resposta = cliente.get(CROSSREF_URL.format(doi=doi_escapado))
    except httpx.HTTPError as exc:
        logger.debug("Verificação de DOI indisponível (%s); seguindo sem ela.", exc)
        return metadados, None

    if resposta.status_code != 200:
        # 404 (não indexado) ou qualquer outro erro: não é evidência de nada,
        # só significa que não dá para verificar.
        return metadados, None

    try:
        dados = resposta.json().get("message", {})
    except ValueError:
        return metadados, None

    titulo_real = " ".join(dados.get("title") or [])
    autores_reais = " ".join(
        f"{a.get('given', '')} {a.get('family', '')}".strip() for a in dados.get("author") or []
    )

    if not (
        _bate(metadados.titulo, titulo_real) or _bate(metadados.autor_principal or "", autores_reais)
    ):
        return metadados, _mensagem_divergencia("DOI", doi, "Crossref", titulo_real, autores_reais)

    enriquecidos = _preencher_lacunas(
        metadados, editora=dados.get("publisher") or "", ano=_ano_da_data_crossref(dados)
    )
    return enriquecidos, None


def _verificar_isbn(
    cliente: httpx.Client, isbn: str, metadados: Metadados
) -> tuple[Metadados, Optional[str]]:
    isbn_limpo = re.sub(r"[\s-]", "", isbn)
    try:
        resposta = cliente.get(
            OPENLIBRARY_URL,
            params={"bibkeys": f"ISBN:{isbn_limpo}", "jscmd": "data", "format": "json"},
        )
    except httpx.HTTPError as exc:
        logger.debug("Verificação de ISBN indisponível (%s); seguindo sem ela.", exc)
        return metadados, None

    if resposta.status_code != 200:
        return metadados, None

    try:
        corpo: dict[str, Any] = resposta.json()
    except ValueError:
        return metadados, None

    dados = corpo.get(f"ISBN:{isbn_limpo}")
    if not dados:
        return metadados, None  # não catalogado — cobertura incompleta, não é erro

    titulo_real = dados.get("title", "")
    autores_reais = " ".join(a.get("name", "") for a in dados.get("authors") or [])

    if not (
        _bate(metadados.titulo, titulo_real) or _bate(metadados.autor_principal or "", autores_reais)
    ):
        return metadados, _mensagem_divergencia(
            "ISBN", isbn, "Open Library", titulo_real, autores_reais
        )

    editoras = dados.get("publishers") or []
    locais = dados.get("publish_places") or []
    enriquecidos = _preencher_lacunas(
        metadados,
        editora=editoras[0].get("name", "") if editoras else "",
        ano=_ano_da_data_openlibrary(dados.get("publish_date")),
        local=locais[0].get("name", "") if locais else "",
    )
    return enriquecidos, None


def _ano_da_data_crossref(dados: dict[str, Any]) -> Optional[int]:
    """Extrai o ano de `published`/`published-print`/`published-online`.

    Formato Crossref: `{"date-parts": [[2019, 5, 1]]}` — só o ano interessa.
    """
    for chave in ("published", "published-print", "published-online"):
        partes = (dados.get(chave) or {}).get("date-parts")
        if partes and partes[0] and partes[0][0]:
            try:
                return int(partes[0][0])
            except (TypeError, ValueError):
                continue
    return None


_PADRAO_ANO = re.compile(r"(?:19|20)\d{2}")


def _ano_da_data_openlibrary(bruto: Optional[str]) -> Optional[int]:
    """`publish_date` da Open Library é texto livre (ex.: "May 2019",
    "2019-05-01", "2019") — extrai os 4 dígitos do ano de onde estiverem."""
    if not bruto:
        return None
    encontrado = _PADRAO_ANO.search(bruto)
    return int(encontrado.group(0)) if encontrado else None


def _preencher_lacunas(
    metadados: Metadados, *, editora: str = "", ano: Optional[int] = None, local: str = ""
) -> Metadados:
    """Preenche `editora_ou_periodico`/`ano`/`local` só onde já é `None`.

    Nunca sobrescreve um valor que o LLM já extraiu — só completa o que
    faltou, e só é chamada depois de confirmar (título/autor batendo) que o
    identificador é da mesma obra.
    """
    atualizacoes: dict[str, Any] = {}
    if not metadados.editora_ou_periodico and editora:
        atualizacoes["editora_ou_periodico"] = editora
    if not metadados.ano and ano:
        atualizacoes["ano"] = ano
    if not metadados.local and local:
        atualizacoes["local"] = local
    if not atualizacoes:
        return metadados
    return metadados.model_copy(update=atualizacoes)


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
