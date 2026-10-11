"""Persistência do progresso de um lote, para permitir retomar com --resume.

Contém tanto a estrutura de estado de execução global do CLI (EstadoDeExecucao)
quanto o gerenciador flexível por diretório (EstadoManager).
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

logger = logging.getLogger(__name__)

#: Caminho padrão para o estado global do CLI
CAMINHO_ESTADO = Path.home() / ".organizador-pdf" / "estado.json"
DEFAULT_STATE_FILENAME = ".organizador_pdf_estado.json"


@contextmanager
def lock_arquivo_estado(timeout: float = 10.0):
    """Bloqueio inter-processos via file lock para coordenar acessos concorrentes ao estado."""
    CAMINHO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    caminho_lock = CAMINHO_ESTADO.with_suffix(".lock")
    with open(caminho_lock, "a") as f:
        start = time.time()
        if _HAS_FCNTL:
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.time() - start >= timeout:
                        raise TimeoutError(
                            f"Não foi possível obter o lock do arquivo de estado ({caminho_lock}) em {timeout}s. "
                            "Outro processo do organizador-pdf pode estar em execução simultânea."
                        )
                    time.sleep(0.05)
        try:
            yield
        finally:
            if _HAS_FCNTL:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


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
    estrutura: str = "cnpq"
    max_paginas: int = 10
    max_caracteres: int = 30_000


@dataclass
class EstadoDeExecucao:
    """Estado global salvo para retomada do comando no terminal."""

    parametros: ParametrosSalvos
    sucessos: set[str] = field(default_factory=set)
    falhas: dict[str, str] = field(default_factory=dict)
    versao: int = 1

    def __init__(
        self,
        parametros: ParametrosSalvos,
        sucessos: Optional[Set[str]] = None,
        falhas: Optional[Dict[str, str]] = None,
        versao: int = 1,
        concluidos: Optional[Set[str]] = None,
    ) -> None:
        self.parametros = parametros
        if sucessos is not None:
            self.sucessos = set(sucessos)
        elif concluidos is not None:
            self.sucessos = set(concluidos)
        else:
            self.sucessos = set()
        self.falhas = dict(falhas) if falhas is not None else {}
        self.versao = versao

    @property
    def concluidos(self) -> set[str]:
        """Alias para compatibilidade retroativa com código e testes existentes."""
        return self.sucessos

    @concluidos.setter
    def concluidos(self, valores: Set[str]) -> None:
        self.sucessos = set(valores)

    def marcar_sucesso(self, caminho: Path | str) -> None:
        """Registra um PDF como concluído com sucesso."""
        abs_p = str(Path(caminho).resolve())
        self.sucessos.add(abs_p)
        self.falhas.pop(abs_p, None)
        self.salvar()

    def marcar_falha(self, caminho: Path | str, erro: str) -> None:
        """Registra uma falha transitória/retentável em um PDF."""
        abs_p = str(Path(caminho).resolve())
        self.falhas[abs_p] = erro
        self.salvar()

    def marcar_concluido(self, caminho: Path | str) -> None:
        """Compatibilidade: registra como sucesso."""
        self.marcar_sucesso(caminho)

    def salvar(self) -> None:
        """Gravação atômica do estado via arquivo temporário + os.replace sob lock de processo."""
        CAMINHO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        dados = {
            "versao": self.versao,
            "parametros": asdict(self.parametros),
            "sucessos": sorted(self.sucessos),
            "falhas": self.falhas,
            "concluidos": sorted(self.sucessos),
        }
        with lock_arquivo_estado():
            arquivo_tmp = CAMINHO_ESTADO.with_name(f"{CAMINHO_ESTADO.name}.{os.getpid()}.tmp")
            try:
                arquivo_tmp.write_text(
                    json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                os.replace(arquivo_tmp, CAMINHO_ESTADO)
            except Exception:
                if arquivo_tmp.exists():
                    arquivo_tmp.unlink(missing_ok=True)
                raise

    @classmethod
    def carregar(cls) -> Optional["EstadoDeExecucao"]:
        """Lê o estado salvo sob lock de processo, ou None se não houver execução pendente."""
        with lock_arquivo_estado():
            if not CAMINHO_ESTADO.exists():
                return None
            try:
                dados = json.loads(CAMINHO_ESTADO.read_text(encoding="utf-8"))
                if not isinstance(dados, dict):
                    return None
                params_raw = dados.get("parametros", {})
                from dataclasses import fields
                valid_field_names = {f.name for f in fields(ParametrosSalvos)}
                filtered_params = {k: v for k, v in params_raw.items() if k in valid_field_names}

                sucessos_raw = dados.get("sucessos")
                if sucessos_raw is None:
                    sucessos_raw = dados.get("concluidos", [])
                sucessos = set(sucessos_raw)
                falhas = dict(dados.get("falhas", {}))
                versao = int(dados.get("versao", 1))

                return cls(
                    parametros=ParametrosSalvos(**filtered_params),
                    sucessos=sucessos,
                    falhas=falhas,
                    versao=versao,
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
                return None

    @staticmethod
    def limpar() -> None:
        with lock_arquivo_estado():
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
            tmp_file = self.state_file.with_name(f"{self.state_file.name}.{os.getpid()}.tmp")
            tmp_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_file, self.state_file)
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
