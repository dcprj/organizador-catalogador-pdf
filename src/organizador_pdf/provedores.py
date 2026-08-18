"""Extratores para provedores pagos de LLM — opcionais, escolhidos via `--provedor`.

O padrão do aplicativo continua sendo `ExtratorOllama` (100% local). Estas
classes existem só para quem opta explicitamente por um modelo pago,
trocando privacidade/custo zero por outra relação de custo/benefício. Todas
aplicam as mesmas duas proteções deterministas contra alucinação que o
extrator local usa (`extractor.pos_processar`) — o provedor ser pago não
significa que o resultado seja confiável sem revisão.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Optional, Protocol

import anthropic
import httpx
from pydantic import ValidationError

from .config import Config, Provedor
from .converter import DocumentoConvertido
from .extractor import (
    PROMPT_SISTEMA,
    TIMEOUT_PADRAO,
    ErroDeExtracao,
    ErroFatalDeAPI,
    ExtratorOllama,
    esquema_json_estrito,
    montar_prompt_usuario,
    pos_processar,
)
from .models import Metadados

logger = logging.getLogger(__name__)


class Extrator(Protocol):
    """Contrato comum a todo extrator, seja qual for o provedor."""

    def extrair(self, documento: DocumentoConvertido) -> Metadados: ...


#: Máximo de tokens de saída — os metadados são um JSON curto, folga generosa
#: sem inflar custo desnecessariamente.
MAX_TOKENS_SAIDA = 4096

#: URL base de cada provedor compatível com a API de chat completions da
#: OpenAI. Cobre a maioria dos provedores pagos com um único adaptador —
#: exceto a Anthropic, cuja API tem formato próprio (ver `ExtratorAnthropic`).
_BASE_URL_POR_PROVEDOR: dict[Provedor, str] = {
    Provedor.OPENAI: "https://api.openai.com/v1",
    Provedor.DEEPSEEK: "https://api.deepseek.com/v1",
    Provedor.GROK: "https://api.x.ai/v1",
    # Camada de compatibilidade da própria Google — evita reimplementar o
    # formato nativo da Gemini API só para este caso de uso.
    Provedor.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai",
}

_NOME_EXIBICAO: dict[Provedor, str] = {
    Provedor.OPENAI: "OpenAI",
    Provedor.DEEPSEEK: "DeepSeek",
    Provedor.GROK: "Grok",
    Provedor.GEMINI: "Gemini",
    Provedor.ANTHROPIC: "Anthropic",
}

#: Provedores cuja API rejeita `response_format: json_schema` estrito (só
#: aceitam `json_object`, sem imposição de esquema no lado do servidor) —
#: confirmado na prática com a DeepSeek: "This response_format type is
#: unavailable now". A validação Pydantic depois da chamada é quem garante o
#: formato nesses casos.
_PROVEDORES_SEM_JSON_SCHEMA_ESTRITO = {Provedor.DEEPSEEK}

#: A DeepSeek exige explicitamente, para o modo `json_object`: a palavra
#: "json" no prompt e um exemplo do formato desejado (ver
#: https://api-docs.deepseek.com/guides/json_mode). Os demais campos do
#: exemplo são fictícios — só a forma do JSON importa aqui.
_EXEMPLO_JSON = {
    "tipo_publicacao": "Livro",
    "area_principal": "Psicologia",
    "subarea": "Logoterapia",
    "titulo": "Em Busca de Sentido",
    "subtitulo": "Um psicólogo no campo de concentração",
    "autores": ["Frankl, Viktor E."],
    "autor_principal": "Frankl, Viktor E.",
    "editora_ou_periodico": "Vozes",
    "ano": 2019,
    "local": "Petrópolis",
    "identificadores": {"isbn": None, "issn": None, "doi": None},
    "referencia_abnt": (
        "FRANKL, Viktor E. Em busca de sentido: um psicólogo no campo de "
        "concentração. Petrópolis: Vozes, 2019."
    ),
}

_INSTRUCAO_MODO_JSON_OBJETO = (
    "\n\n## Formato da resposta\n"
    "Responda em JSON. Devolva SOMENTE um objeto json válido, sem nenhum "
    "texto antes ou depois, exatamente com estas chaves (exemplo de "
    "formato — os valores abaixo são fictícios):\n"
    + json.dumps(_EXEMPLO_JSON, ensure_ascii=False, indent=2)
)


class ExtratorAnthropic:
    """Envia o trecho inicial do documento à API da Anthropic (Claude)."""

    def __init__(self, config: Config, cliente_http: Optional[httpx.Client] = None) -> None:
        self.config = config
        kwargs: dict[str, Any] = {"api_key": config.api_key}
        if cliente_http is not None:
            kwargs["http_client"] = cliente_http
        self.cliente = anthropic.Anthropic(**kwargs)

    def extrair(self, documento: DocumentoConvertido) -> Metadados:
        if not documento.markdown_inicial.strip():
            raise ErroDeExtracao("trecho inicial vazio; nada para analisar")

        try:
            resposta = self.cliente.messages.parse(
                model=self.config.modelo,
                max_tokens=MAX_TOKENS_SAIDA,
                temperature=0,
                system=PROMPT_SISTEMA,
                messages=[{"role": "user", "content": montar_prompt_usuario(documento)}],
                output_format=Metadados,
            )
        except anthropic.AuthenticationError as exc:
            raise ErroFatalDeAPI(
                "chave de API da Anthropic inválida ou ausente. Confira --apikey "
                "ou ORGPDF_ANTHROPIC_API_KEY."
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            raise ErroFatalDeAPI(
                "a chave de API da Anthropic não tem permissão para este modelo/conta."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ErroFatalDeAPI(
                f"modelo '{self.config.modelo}' não encontrado na Anthropic."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ErroFatalDeAPI(
                "limite de requisições da Anthropic atingido — aguarde e tente "
                "novamente."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ErroFatalDeAPI(
                f"não foi possível conectar à API da Anthropic: {exc}"
            ) from exc
        except anthropic.BadRequestError as exc:
            # "insufficient credit balance" chega como 400, não 402 — sem este
            # caso especial, o mesmo erro fatal se repetiria por arquivo em
            # vez de interromper o lote imediatamente.
            if "credit balance" in str(exc).lower():
                raise ErroFatalDeAPI("saldo de créditos da Anthropic insuficiente.") from exc
            raise ErroDeExtracao(f"requisição rejeitada pela Anthropic: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise ErroDeExtracao("tempo esgotado aguardando a Anthropic") from exc
        except anthropic.APIStatusError as exc:
            raise ErroDeExtracao(f"erro da Anthropic ({exc.status_code}): {exc.message}") from exc
        except ValidationError as exc:
            raise ErroDeExtracao(f"metadados fora do esquema esperado: {exc}") from exc

        metadados = resposta.parsed_output
        if metadados is None:
            raise ErroDeExtracao("a Anthropic não devolveu metadados válidos para o esquema")

        return pos_processar(metadados, documento.markdown_inicial)


class ExtratorOpenAICompativel:
    """Envia o trecho inicial do documento a um provedor compatível com a API
    de chat completions da OpenAI (OpenAI, DeepSeek, Gemini via camada de
    compatibilidade do Google, ou Grok/xAI).

    Suporte a saída estruturada estrita (`response_format: json_schema`)
    varia entre esses provedores — a validação Pydantic logo depois da
    chamada é quem garante que uma resposta fora do esquema vira um erro
    claro por arquivo, em vez de dado corrompido silencioso.
    """

    def __init__(self, config: Config, cliente: Optional[httpx.Client] = None) -> None:
        self.config = config
        self.nome = _NOME_EXIBICAO[config.provedor]
        self._esquema = esquema_json_estrito(Metadados)
        self.cliente = cliente or httpx.Client(
            base_url=_BASE_URL_POR_PROVEDOR[config.provedor], timeout=TIMEOUT_PADRAO
        )
        # Setado à parte (em vez de no construtor do cliente acima) para
        # valer também quando um cliente é injetado (testes, por exemplo) —
        # sem isso, um cliente injetado ficaria sem autenticação.
        self.cliente.headers["Authorization"] = f"Bearer {config.api_key}"

    def extrair(self, documento: DocumentoConvertido) -> Metadados:
        if not documento.markdown_inicial.strip():
            raise ErroDeExtracao("trecho inicial vazio; nada para analisar")

        conteudo_usuario = montar_prompt_usuario(documento)
        if self.config.provedor in _PROVEDORES_SEM_JSON_SCHEMA_ESTRITO:
            response_format: dict[str, Any] = {"type": "json_object"}
            conteudo_usuario += _INSTRUCAO_MODO_JSON_OBJETO
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "metadados_bibliograficos",
                    "strict": True,
                    "schema": self._esquema,
                },
            }

        corpo = {
            "model": self.config.modelo,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": conteudo_usuario},
            ],
            "response_format": response_format,
            "temperature": 0,
        }

        resposta = self._chamar(corpo)

        try:
            texto = resposta["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ErroDeExtracao(
                f"resposta da {self.nome} em formato inesperado: {resposta}"
            ) from exc
        if not texto or not texto.strip():
            raise ErroDeExtracao(f"a {self.nome} não devolveu conteúdo")

        try:
            metadados = Metadados.model_validate(json.loads(texto))
        except json.JSONDecodeError as exc:
            raise ErroDeExtracao(f"resposta da {self.nome} não é JSON válido: {exc}") from exc
        except ValidationError as exc:
            raise ErroDeExtracao(f"metadados fora do esquema esperado: {exc}") from exc

        return pos_processar(metadados, documento.markdown_inicial)

    def _chamar(self, corpo: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = self.cliente.post("/chat/completions", json=corpo)
            resposta.raise_for_status()
        except httpx.ConnectError as exc:
            raise ErroFatalDeAPI(
                f"não foi possível conectar à API da {self.nome}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            codigo = exc.response.status_code
            if codigo in (401, 403):
                raise ErroFatalDeAPI(
                    f"chave de API da {self.nome} inválida ou sem permissão para "
                    "este modelo."
                ) from exc
            if codigo == 404:
                raise ErroFatalDeAPI(
                    f"modelo '{self.config.modelo}' não encontrado na {self.nome}."
                ) from exc
            if codigo == 429:
                raise ErroFatalDeAPI(
                    f"limite de requisições da {self.nome} atingido — aguarde e "
                    "tente novamente."
                ) from exc
            raise ErroDeExtracao(f"erro da {self.nome} ({codigo}): {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            raise ErroDeExtracao(
                f"tempo esgotado aguardando a {self.nome} ({TIMEOUT_PADRAO:.0f}s)"
            ) from exc
        except httpx.HTTPError as exc:
            raise ErroDeExtracao(f"falha de conexão com a {self.nome}: {exc}") from exc

        return resposta.json()


def criar_extrator(config: Config) -> Extrator:
    """Escolhe a implementação de extrator certa para `config.provedor`."""
    if config.provedor is Provedor.OLLAMA:
        return ExtratorOllama(config)
    if config.provedor is Provedor.ANTHROPIC:
        return ExtratorAnthropic(config)
    return ExtratorOpenAICompativel(config)


def criar_extrator_fallback(config: Config) -> Optional[Extrator]:
    """Extrator de fallback (`config.provedor_fallback`), ou None se desligado.

    Reaproveita `criar_extrator` construindo uma variante de `config` com o
    provedor/modelo/chave trocados pelos de fallback — as classes de
    extrator não precisam saber que são "o fallback", só recebem uma config
    normal.
    """
    if config.provedor_fallback is None:
        return None
    config_fallback = dataclasses.replace(
        config,
        provedor=config.provedor_fallback,
        modelo=config.modelo_fallback or config.modelo,
        api_key=config.api_key_fallback,
    )
    return criar_extrator(config_fallback)
