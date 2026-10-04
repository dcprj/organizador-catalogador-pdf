"""Persistência do progresso de um lote, para permitir retomar com --resume.

Contém tanto a estrutura de estado de execução global do CLI (EstadoDeExecucao)
quanto o gerenciador flexível por diretório (EstadoManager).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

#: Caminho padrão para o estado global do CLI
CAMINHO_ESTADO = Path.home() / ".organizador-pdf" / "estado.json"
DEFAULT_STATE_FILENAME = ".organizador_pdf_estado.json"


@dataclass
class ParametrosSalvos:
    """Parâmetros da execução original, reaplicados por `--resume`."""

    origem: str
    destino: str
    dry_run: bool = False
    recursive: bool = True
    mover: bool = False
    subpasta_markdown: Optional[str] = None
    paralelo: int = 1
    quarantine: bool = True
    enriquecimento_online: bool = True
    modelo: Optional[str] = None
    ollama_url: Optional[str] = None
    provedor: Optional[str] = None
    provedor_fallback: Optional[str] = None
    modelo_fallback: Optional[str] = None


@dataclass
class EstadoDeExecucao:
    """Estado global salvo para retomada do comando no terminal."""

    parametros: ParametrosSalvos
    concluidos: set[str] = field(default_factory=set)

    def marcar_concluido(self, caminho: Path) -> None:
        """Registra um PDF como definitivamente processado."""
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
        """Lê o estado salvo, ou None se não houver execução pendente."""
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


class EstadoManager:
    """Gerenciador de estado e checkpoints para testes e processamento em lote."""

    def __init__(self, state_file: Optional[Path] = None, base_dir: Optional[Path] = None):
        if state_file:
            self.state_file = Path(state_file).resolve()
        elif base_dir:
            self.state_file = (Path(base_dir).resolve() / DEFAULT_STATE_FILENAME)
        else:
            self.state_file = (Path.home() / DEFAULT_STATE_FILENAME).resolve()

        self._state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        """Carrega estado do arquivo JSON se existir."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.warning("Não foi possível ler o arquivo de estado %s (%s). Iniciando novo.", self.state_file, e)
        return {
            "started_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "completed_files": {},
        }

    def save_checkpoint(
        self,
        original_pdf: str,
        target_pdf: str,
        classification: str,
        success: bool,
    ) -> None:
        """Registra arquivo concluído no checkpoint."""
        abs_path = str(Path(original_pdf).resolve())
        self._state["last_updated"] = datetime.now().isoformat()
        self._state.setdefault("completed_files", {})[abs_path] = {
            "target_pdf": target_pdf,
            "classification": classification,
            "success": success,
            "timestamp": datetime.now().isoformat(),
        }

        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error("Falha ao salvar checkpoint em %s: %s", self.state_file, e)

    def is_completed(self, original_pdf: str) -> bool:
        """Verifica se o arquivo já foi concluído."""
        abs_path = str(Path(original_pdf).resolve())
        info = self._state.get("completed_files", {}).get(abs_path)
        return bool(info and info.get("success"))

    def get_completed_count(self) -> int:
        """Retorna o número de arquivos concluídos com sucesso."""
        return sum(1 for item in self._state.get("completed_files", {}).values() if item.get("success"))

    def clear(self) -> None:
        """Remove o arquivo de estado ao concluir lote limpo."""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                logger.info("Lote concluído com sucesso. Estado removido: %s", self.state_file)
        except Exception as e:
            logger.warning("Falha ao remover arquivo de estado %s: %s", self.state_file, e)
