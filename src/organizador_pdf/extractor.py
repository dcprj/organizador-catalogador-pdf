"""Exceções e adaptadores de extração de metadados."""

from __future__ import annotations


class ErroDeExtracao(RuntimeError):
    """Falha ao extrair metadados do documento."""


class ErroFatalDeAPI(RuntimeError):
    """Erro irrecuperável que afeta todas as chamadas seguintes."""
