"""Configuração de log: console legível + arquivo `erros.log`."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

FORMATO_ARQUIVO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configurar_logs(
    arquivo_erros: Optional[Path] = None,
    *,
    arquivo_log: Optional[Path] = None,
    verboso: bool = False,
    verbose: bool = False,
    **kwargs,
) -> Console:
    """Liga o log do console (INFO/DEBUG) e o de erros em arquivo (>= WARNING)."""
    arquivo_final = arquivo_erros or arquivo_log or Path("erros.log")
    is_verboso = verboso or verbose
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
    console_handler.setLevel(logging.DEBUG if is_verboso else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    raiz.addHandler(console_handler)

    try:
        arquivo_final.parent.mkdir(parents=True, exist_ok=True)
        arquivo_handler = logging.FileHandler(arquivo_final, encoding="utf-8")
        arquivo_handler.setLevel(logging.WARNING)
        arquivo_handler.setFormatter(logging.Formatter(FORMATO_ARQUIVO))
        raiz.addHandler(arquivo_handler)
    except OSError as exc:
        console.print(
            f"[yellow]Aviso:[/] não foi possível abrir {arquivo_final} para log: {exc}"
        )

    # httpx/httpcore são ruidosos em DEBUG (o modo --verbose sobe o root pra
    # DEBUG) — e, com provedores pagos, o tráfego passa a carregar o header
    # Authorization com a chave de API. Silenciar os dois evita que ela
    # apareça no console em modo verboso.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return console
