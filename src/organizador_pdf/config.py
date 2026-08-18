"""Configuração da aplicação, carregada de variáveis de ambiente / arquivo .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv

#: Modelo local padrão (via Ollama): o que melhor equilibra qualidade e RAM
#: em máquinas com 8 GB. Baixe com `ollama pull qwen2.5:3b-instruct`.
MODELO_PADRAO = "qwen2.5:3b-instruct"

#: Endereço padrão do servidor Ollama.
OLLAMA_URL_PADRAO = "http://localhost:11434"


class Provedor(str, Enum):
    """De onde vem o LLM usado na extração de metadados.

    `OLLAMA` é o único que roda 100% localmente, sem chave de API e sem
    custo por token — os demais são provedores pagos, opcionais, escolhidos
    explicitamente pelo usuário (ver `--provedor`/`ORGPDF_PROVEDOR`).
    """

    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    GROK = "grok"


class ErroDeConfiguracao(RuntimeError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Config:
    """Parâmetros de execução resolvidos a partir do ambiente.

    Este aplicativo usa exclusivamente um LLM local via Ollama — sem chaves
    de API, sem custo por token, sem enviar dados para fora da máquina.
    """

    modelo: str = MODELO_PADRAO
    max_paginas: int = 6
    max_caracteres: int = 15_000
    ollama_url: str = OLLAMA_URL_PADRAO
    #: Confere ISBN/DOI extraídos contra Crossref/Open Library (grátis, sem
    #: chave). É a única chamada de rede do app além do próprio Ollama — só
    #: envia o identificador, nunca o conteúdo do PDF. Desligue com
    #: ORGPDF_VERIFICAR_ONLINE=false para manter tudo 100% offline.
    verificar_online: bool = True
    #: Qual LLM usar na extração. O padrão (`OLLAMA`) é local e gratuito;
    #: os demais são provedores pagos, opcionais — exigem `api_key`.
    provedor: Provedor = Provedor.OLLAMA
    #: Chave de API do provedor pago escolhido. Sempre None para `OLLAMA`.
    api_key: Optional[str] = None
    #: Provedor pago acionado só quando a extração do `provedor` principal
    #: falha ou sai com aviso de divergência (ver `--provedor-fallback`).
    #: None (padrão) desliga o recurso — nenhuma chamada extra é feita.
    provedor_fallback: Optional[Provedor] = None
    modelo_fallback: Optional[str] = None
    api_key_fallback: Optional[str] = None
    #: Recorre a OCR (Tesseract) para PDFs sem texto extraível (digitalizados).
    #: Só tem efeito se o Tesseract estiver instalado — sem isso, o
    #: comportamento é o de sempre (falha explícita). Desligue com
    #: ORGPDF_OCR=false para nunca tentar, mesmo com o Tesseract presente.
    ocr: bool = True
    #: Idioma do OCR, no formato de 3 letras do Tesseract (ex.: "por", "eng").
    ocr_idioma: str = "por"

    @classmethod
    def do_ambiente(
        cls,
        env_file: Optional[Path] = None,
        *,
        modelo: Optional[str] = None,
        ollama_url: Optional[str] = None,
        verificar_online: Optional[bool] = None,
        provedor: Optional[str] = None,
        api_key: Optional[str] = None,
        provedor_fallback: Optional[str] = None,
        modelo_fallback: Optional[str] = None,
        api_key_fallback: Optional[str] = None,
        ocr: Optional[bool] = None,
        ocr_idioma: Optional[str] = None,
    ) -> "Config":
        """Carrega a configuração do `.env` e do ambiente.

        Os parâmetros nomeados vêm da linha de comando e têm precedência
        sobre o ambiente, para permitir testar uma combinação diferente sem
        editar o `.env`.
        """
        # Sem `env_file` explícito, busca a partir do diretório onde o
        # usuário está rodando o comando (usecwd=True) — não a partir de onde
        # este pacote está instalado. Sem isso, uma instalação não-editável
        # (pacote em site-packages) nunca encontraria o `.env` do projeto.
        #
        # Importante: só chamamos load_dotenv() quando um caminho foi de fato
        # encontrado. Passar dotenv_path=None faz a biblioteca rodar sua
        # PRÓPRIA busca interna (a partir do arquivo deste módulo, não do
        # cwd) — reintroduzindo o mesmo problema que o usecwd=True corrige.
        caminho_env = env_file or find_dotenv(usecwd=True) or None
        if caminho_env:
            load_dotenv(dotenv_path=caminho_env, override=False)

        provedor_resolvido = _provedor(provedor, "ORGPDF_PROVEDOR", Provedor.OLLAMA)
        modelo_resolvido = _modelo(modelo, "ORGPDF_MODELO", provedor_resolvido)
        api_key_resolvida = _api_key(api_key, provedor_resolvido)

        # Fallback é opt-in: só existe se um provedor foi de fato indicado
        # (CLI ou ORGPDF_PROVEDOR_FALLBACK) — sem isso, nenhum modelo/chave é
        # exigido e o recurso fica completamente desligado.
        provedor_fallback_resolvido = _provedor(
            provedor_fallback, "ORGPDF_PROVEDOR_FALLBACK", None
        )
        modelo_fallback_resolvido: Optional[str] = None
        api_key_fallback_resolvida: Optional[str] = None
        if provedor_fallback_resolvido is not None:
            # Variável própria (ORGPDF_MODELO_FALLBACK) — não pode cair para
            # ORGPDF_MODELO, que é do provedor *principal* e quase certamente
            # um modelo incompatível com o provedor de fallback.
            modelo_fallback_resolvido = _modelo(
                modelo_fallback, "ORGPDF_MODELO_FALLBACK", provedor_fallback_resolvido
            )
            api_key_fallback_resolvida = _api_key(api_key_fallback, provedor_fallback_resolvido)

        return cls(
            modelo=modelo_resolvido,
            max_paginas=_inteiro_positivo("ORGPDF_MAX_PAGINAS", 6),
            max_caracteres=_inteiro_positivo("ORGPDF_MAX_CARACTERES", 15_000),
            ollama_url=(ollama_url or os.getenv("ORGPDF_OLLAMA_URL") or OLLAMA_URL_PADRAO).strip()
            or OLLAMA_URL_PADRAO,
            verificar_online=(
                verificar_online
                if verificar_online is not None
                else _booleano("ORGPDF_VERIFICAR_ONLINE", True)
            ),
            provedor=provedor_resolvido,
            api_key=api_key_resolvida,
            provedor_fallback=provedor_fallback_resolvido,
            modelo_fallback=modelo_fallback_resolvido,
            api_key_fallback=api_key_fallback_resolvida,
            ocr=ocr if ocr is not None else _booleano("ORGPDF_OCR", True),
            ocr_idioma=(ocr_idioma or os.getenv("ORGPDF_OCR_IDIOMA") or "por").strip() or "por",
        )


def _provedor(
    bruto: Optional[str], variavel_ambiente: str, padrao: Optional[Provedor]
) -> Optional[Provedor]:
    valor = (bruto or os.getenv(variavel_ambiente) or "").strip().lower()
    if not valor:
        return padrao
    try:
        return Provedor(valor)
    except ValueError as exc:
        opcoes = ", ".join(p.value for p in Provedor)
        raise ErroDeConfiguracao(
            f"provedor {valor!r} não reconhecido. Use um de: {opcoes}."
        ) from exc


def _modelo(bruto: Optional[str], variavel_ambiente: str, provedor: Provedor) -> str:
    valor = (bruto or os.getenv(variavel_ambiente) or "").strip()
    if valor:
        return valor
    if provedor is Provedor.OLLAMA:
        return MODELO_PADRAO
    raise ErroDeConfiguracao(
        f"o provedor {provedor.value!r} exige um modelo explícito — use "
        "--modelo ou ORGPDF_MODELO (ex.: claude-sonnet-5, gpt-5-mini, "
        "deepseek-v4-flash, gemini-2.5-flash, grok-4)."
    )


def _api_key(bruto: Optional[str], provedor: Provedor) -> Optional[str]:
    if provedor is Provedor.OLLAMA:
        return None
    variavel_especifica = f"ORGPDF_{provedor.value.upper()}_API_KEY"
    valor = (
        bruto or os.getenv(variavel_especifica) or os.getenv("ORGPDF_API_KEY") or ""
    ).strip()
    if valor:
        return valor
    raise ErroDeConfiguracao(
        f"o provedor {provedor.value!r} exige uma chave de API — use --apikey, "
        f"ou defina {variavel_especifica} (ou ORGPDF_API_KEY) no .env."
    )


def _booleano(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome)
    if bruto is None or not bruto.strip():
        return padrao
    valor = bruto.strip().lower()
    if valor in ("1", "true", "sim", "on", "verdadeiro"):
        return True
    if valor in ("0", "false", "nao", "não", "off", "falso"):
        return False
    raise ErroDeConfiguracao(
        f"{nome} deve ser um valor booleano (true/false), recebi {bruto!r}."
    )


def _inteiro_positivo(nome: str, padrao: int) -> int:
    bruto = os.getenv(nome)
    if bruto is None or not bruto.strip():
        return padrao
    try:
        valor = int(bruto)
    except ValueError as exc:
        raise ErroDeConfiguracao(f"{nome} deve ser um número inteiro, recebi {bruto!r}.") from exc
    if valor <= 0:
        raise ErroDeConfiguracao(f"{nome} deve ser maior que zero, recebi {valor}.")
    return valor
