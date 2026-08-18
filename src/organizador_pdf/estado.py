"""Persistência do progresso de um lote, para permitir retomar com --resume.

Guarda os parâmetros da execução e o caminho de cada PDF já concluído (com
sucesso ou falha definitiva) em um único arquivo, atualizado incrementalmente
a cada arquivo — assim uma interrupção (Ctrl+C, queda, erro fatal de API)
deixa o progresso salvo até o último arquivo terminado. Nunca guarda chaves
de API: só os demais parâmetros de linha de comando.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

#: Um único slot global — "a última execução" — e não por origem/destino,
#: para bater com o pedido de simplesmente `--resume` sem precisar informar
#: nada de novo.
CAMINHO_ESTADO = Path.home() / ".organizador-pdf" / "estado.json"


@dataclass
class ParametrosSalvos:
    """Parâmetros da execução original, reaplicados por `--resume`.

    Propositalmente sem `api_key`/`api_key_fallback` — nunca persiste
    segredo em disco. Se a chave foi passada via `--apikey` (não via
    `.env`), o `--resume` volta a exigi-la.
    """

    origem: str
    destino: str
    dry_run: bool
    recursive: bool
    mover: bool
    subpasta_markdown: Optional[str]
    modelo: Optional[str]
    ollama_url: Optional[str]
    provedor: Optional[str]
    provedor_fallback: Optional[str]
    modelo_fallback: Optional[str]


@dataclass
class EstadoDeExecucao:
    parametros: ParametrosSalvos
    concluidos: set[str] = field(default_factory=set)

    def marcar_concluido(self, caminho: Path) -> None:
        """Registra um PDF como definitivamente processado (sucesso ou falha).

        Falhas contam como concluídas de propósito: `--resume` não repete
        automaticamente um arquivo que já falhou de forma definitiva (ex.:
        PDF corrompido) — só retoma o que ficou pra trás pela interrupção
        em si. Para forçar nova tentativa nos que falharam, rode sem
        `--resume`.
        """
        self.concluidos.add(str(caminho.resolve()))
        self.salvar()

    def salvar(self) -> None:
        CAMINHO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        dados = {
            "parametros": asdict(self.parametros),
            "concluidos": sorted(self.concluidos),
        }
        CAMINHO_ESTADO.write_text(
            json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def carregar(cls) -> Optional["EstadoDeExecucao"]:
        """Lê o estado salvo, ou `None` se não houver execução pendente."""
        if not CAMINHO_ESTADO.exists():
            return None
        try:
            dados = json.loads(CAMINHO_ESTADO.read_text(encoding="utf-8"))
            return cls(
                parametros=ParametrosSalvos(**dados["parametros"]),
                concluidos=set(dados.get("concluidos", [])),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def limpar() -> None:
        CAMINHO_ESTADO.unlink(missing_ok=True)
