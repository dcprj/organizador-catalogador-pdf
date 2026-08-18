"""Configuração de log: console legível + arquivo `erros.log`."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

FORMATO_ARQUIVO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configurar_logs(arquivo_erros: Path, *, verboso: bool = False) -> Console:
    """Liga o log do console (INFO/DEBUG) e o de erros em arquivo (>= WARNING)."""
    console = Console(stderr=True)

    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG)
    for handler in list(raiz.handlers):
        raiz.removeHandler(handler)

    console_handler = RichHandler(
        console=console,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    console_handler.setLevel(logging.DEBUG if verboso else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    raiz.addHandler(console_handler)

    try:
        arquivo_erros.parent.mkdir(parents=True, exist_ok=True)
        arquivo_handler = logging.FileHandler(arquivo_erros, encoding="utf-8")
        arquivo_handler.setLevel(logging.WARNING)
        arquivo_handler.setFormatter(logging.Formatter(FORMATO_ARQUIVO))
        raiz.addHandler(arquivo_handler)
    except OSError as exc:
        console.print(
            f"[yellow]Aviso:[/] não foi possível abrir {arquivo_erros} para log: {exc}"
        )

    # A biblioteca HTTP usada para falar com o Ollama é ruidosa em nível INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return console
