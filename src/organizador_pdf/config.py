"""Configuração da aplicação, carregada de variáveis de ambiente / arquivo .env."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import find_dotenv, load_dotenv


class ErroDeConfiguracao(RuntimeError):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Config:
    """Parâmetros de execução resolvidos a partir do ambiente e da linha de comando.

    Controla os limites de amostragem textual para classificação/metadados,
    a validação bibliográfica externa (APIs públicas) e a chave opcional
    de integração remota com o classificador Jev (TypeSafe AI).
    """

    max_paginas: int = 10
    max_caracteres: int = 30_000
    #: Confere ISBN/DOI/título extraídos contra bases bibliográficas públicas
    #: (Brasil API / CBL, Crossref, Google Books, OpenAlex, OpenLibrary).
    #: NUNCA envia o arquivo PDF nem texto integral — apenas identificadores/título.
    #: Desligue com ORGPDF_VERIFICAR_ONLINE=false para manter 100% offline.
    verificar_online: bool = True
    #: Chave de API opcional para classificação semântica remota via TypeSafe AI.
    #: Se ausente (None), o classificador Jev opera 100% localmente via regras
    #: probabilísticas calibradas (System One Heuristics).
    typesafe_api_key: Optional[str] = None

    @property
    def modelo(self) -> str:
        """Identificador do modelo classificador em execução."""
        return "jev-typesafe" if self.typesafe_api_key else "jev-system-one-local"

    @property
    def provedor(self) -> str:
        """Provedor do motor de classificação."""
        return "typesafe" if self.typesafe_api_key else "deterministico_local"

    @property
    def total_offline(self) -> bool:
        """Indica se a execução é 100% desconectada da rede (sem consultas bibliográficas e sem TypeSafe AI)."""
        return (not self.verificar_online) and (not self.typesafe_api_key)

    @classmethod
    def do_ambiente(
        cls,
        env_file: Optional[Path] = None,
        *,
        verificar_online: Optional[bool] = None,
        max_paginas: Optional[int] = None,
        max_caracteres: Optional[int] = None,
        typesafe_api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> "Config":
        """Carrega a configuração do `.env` e do ambiente.

        Argumentos passados explicitamente têm precedência sobre as variáveis de ambiente.
        Opções legadas de versões anteriores (< v0.4.0) são ignoradas com aviso de descontinuação.
        """
        if kwargs:
            for k in kwargs:
                warnings.warn(
                    f"A opção de configuração '{k}' é legada e foi descontinuada na v0.4.0+. "
                    f"O organizador agora utiliza o motor determinístico Jev com APIs bibliográficas públicas.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        caminho_env = env_file or find_dotenv(usecwd=True) or None
        if caminho_env:
            load_dotenv(dotenv_path=caminho_env, override=False)

        key = (
            typesafe_api_key
            or os.getenv("TYPESAFE_API_KEY")
            or os.getenv("ORGPDF_TYPESAFE_API_KEY")
        )
        if key:
            key = key.strip() or None

        return cls(
            max_paginas=_inteiro_positivo("ORGPDF_MAX_PAGINAS", 10, cli=max_paginas),
            max_caracteres=_inteiro_positivo(
                "ORGPDF_MAX_CARACTERES", 30_000, cli=max_caracteres
            ),
            verificar_online=(
                verificar_online
                if verificar_online is not None
                else _booleano("ORGPDF_VERIFICAR_ONLINE", True)
            ),
            typesafe_api_key=key,
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


def _inteiro_positivo(nome: str, padrao: int, *, cli: Optional[int] = None) -> int:
    if cli is not None:
        valor = cli
    else:
        bruto = os.getenv(nome)
        if bruto is None or not bruto.strip():
            return padrao
        try:
            valor = int(bruto)
        except ValueError as exc:
            raise ErroDeConfiguracao(
                f"{nome} deve ser um número inteiro, recebi {bruto!r}."
            ) from exc
    if valor <= 0:
        raise ErroDeConfiguracao(f"{nome} deve ser maior que zero, recebi {valor}.")
    return valor
