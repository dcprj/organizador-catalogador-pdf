"""Pipeline de processamento por arquivo, resiliente a falhas individuais."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .config import Config
from .converter import DocumentoConvertido, ErroDeConversao, converter_pdf
from .extractor import ErroDeExtracao, ErroFatalDeAPI
from .models import Metadados
from .organizer import ErroDeOrganizacao, gerar_markdown, organizar
from .provedores import criar_extrator
from .verificacao import verificar_identificadores

logger = logging.getLogger(__name__)

#: Palavras comuns demais para servir de sinal de identidade do documento —
#: conectores, tipos de documento e palavras de título acadêmico genéricas
#: o bastante para aparecer em praticamente qualquer trabalho.
_PALAVRAS_IRRELEVANTES = {
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "para", "com", "sem", "por", "que", "uma", "um", "sobre", "entre",
    "the", "and", "for", "with", "from",
    "pdf", "livro", "livros", "artigo", "artigos", "revista", "capitulo",
    "final", "versao", "version", "copy", "copia", "documento", "scan",
    "download", "original",
    "analise", "estudo", "estudos", "abordagem", "reflexao", "reflexoes",
    "consideracoes", "aspectos", "introducao", "panorama", "revisao",
    "ensaio", "perspectiva", "perspectivas", "questao", "questoes",
    "problema", "problemas", "tema", "temas", "caso", "presente",
    "trabalho", "pesquisa", "investigacao", "historia", "sociedade",
    "conceito", "evolucao", "constituicao",
}

#: Abaixo disso, o overlap de palavras não é evidência confiável o
#: suficiente — uma única palavra em comum pode ser coincidência.
_MIN_PALAVRAS_EM_COMUM = 2


class Situacao(str, Enum):
    SUCESSO = "sucesso"
    SIMULADO = "simulado"
    FALHA = "falha"


@dataclass
class ResultadoDoArquivo:
    """O que aconteceu com um PDF do lote."""

    origem: Path
    situacao: Situacao
    metadados: Optional[Metadados] = None
    pdf_destino: Optional[Path] = None
    markdown_destino: Optional[Path] = None
    erro: Optional[str] = None
    etapa: Optional[str] = None
    aviso: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.situacao is not Situacao.FALHA


@dataclass
class OpcoesDoPipeline:
    """Parâmetros de execução escolhidos na linha de comando."""

    destino: Path
    dry_run: bool = False
    mover: bool = False
    subpasta_markdown: Optional[str] = None


class Pipeline:
    """Executa converter → extrair → gerar Markdown → organizar."""

    def __init__(
        self,
        config: Config,
        opcoes: OpcoesDoPipeline,
        extrator: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.opcoes = opcoes
        self.extrator = extrator or criar_extrator(config)

    def processar_arquivo(self, caminho: Path) -> ResultadoDoArquivo:
        """Processa um PDF, devolvendo o erro no resultado em vez de propagá-lo.

        A única exceção que escapa é `ErroFatalDeAPI` (credencial, permissão,
        modelo inexistente): ela afeta todo o lote, então repetir a tentativa
        arquivo a arquivo só desperdiçaria tempo.
        """
        etapa = "conversão"
        try:
            documento = converter_pdf(
                caminho,
                paginas_para_analise=self.config.max_paginas,
                max_caracteres_analise=self.config.max_caracteres,
            )

            etapa = "extração de metadados"
            metadados = self.extrator.extrair(documento)

            aviso = _titulo_diverge_do_arquivo(caminho.name, metadados)

            if self.config.verificar_online:
                aviso_online = verificar_identificadores(metadados)
                if aviso_online:
                    aviso = f"{aviso} Além disso, {aviso_online}" if aviso else aviso_online

            if aviso:
                logger.warning("[%s] %s", caminho.name, aviso)

            etapa = "geração do Markdown"
            markdown = self._montar_markdown(metadados, documento)

            etapa = "organização"
            resultado = organizar(
                metadados,
                pdf_origem=caminho,
                destino=self.opcoes.destino,
                markdown=markdown,
                subpasta_markdown=self.opcoes.subpasta_markdown,
                mover=self.opcoes.mover,
                dry_run=self.opcoes.dry_run,
            )

            return ResultadoDoArquivo(
                origem=caminho,
                situacao=Situacao.SIMULADO if resultado.simulado else Situacao.SUCESSO,
                metadados=metadados,
                pdf_destino=resultado.pdf_destino,
                markdown_destino=resultado.markdown_destino,
                aviso=aviso,
            )

        except ErroFatalDeAPI:
            raise
        except (ErroDeConversao, ErroDeExtracao, ErroDeOrganizacao) as exc:
            return self._falha(caminho, etapa, str(exc))
        except Exception as exc:  # noqa: BLE001 - um PDF não pode derrubar o lote
            logger.debug("Erro inesperado em %s", caminho, exc_info=True)
            return self._falha(caminho, etapa, f"{type(exc).__name__}: {exc}")

    def processar_lote(
        self,
        caminhos: Iterable[Path],
        *,
        ao_concluir: Optional[Callable[[ResultadoDoArquivo], None]] = None,
    ) -> list[ResultadoDoArquivo]:
        """Processa vários PDFs em sequência, sem interromper em caso de falha."""
        resultados: list[ResultadoDoArquivo] = []
        for caminho in caminhos:
            resultado = self.processar_arquivo(caminho)
            resultados.append(resultado)
            if ao_concluir:
                ao_concluir(resultado)
        return resultados

    def _montar_markdown(
        self, metadados: Metadados, documento: DocumentoConvertido
    ) -> str:
        return gerar_markdown(
            metadados,
            documento.markdown_completo,
            arquivo_origem=documento.caminho,
            total_paginas=documento.total_paginas,
        )

    @staticmethod
    def _falha(caminho: Path, etapa: str, mensagem: str) -> ResultadoDoArquivo:
        logger.error("[%s] %s — %s", etapa, caminho.name, mensagem)
        return ResultadoDoArquivo(
            origem=caminho, situacao=Situacao.FALHA, erro=mensagem, etapa=etapa
        )


def _tokenizar(texto: str) -> set[str]:
    """Normaliza um texto em um conjunto de palavras significativas (>=4 letras)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    palavras = re.findall(r"[a-zA-Z]{4,}", sem_acento.lower())
    return {p for p in palavras if p not in _PALAVRAS_IRRELEVANTES}


def _titulo_diverge_do_arquivo(nome_arquivo: str, metadados: Metadados) -> Optional[str]:
    """Sinal heurístico (não uma prova) de que o modelo catalogou outra obra.

    Compara palavras significativas do nome do arquivo original com o texto
    combinado dos metadados extraídos (título, subtítulo, autor, editora). Se
    o nome do arquivo carrega conteúdo real e quase nenhuma palavra bate, é um
    indício de que a extração pegou dados de uma obra citada/discutida no
    corpo do texto em vez da própria obra — foi assim que alucinações reais
    foram identificadas durante o desenvolvimento desta ferramenta. Não
    bloqueia o processamento, só sinaliza para revisão manual — nomes de
    arquivo genéricos, em outro idioma ou muito diferentes do título por
    razões legítimas podem gerar avisos que não são de fato um problema.
    """
    tokens_arquivo = _tokenizar(Path(nome_arquivo).stem)
    if len(tokens_arquivo) < _MIN_PALAVRAS_EM_COMUM:
        return None  # nome genérico ou curto demais para servir de sinal

    texto_metadados = " ".join(
        filter(
            None,
            [
                metadados.titulo,
                metadados.subtitulo,
                metadados.autor_principal,
                metadados.editora_ou_periodico,
            ],
        )
    )
    tokens_metadados = _tokenizar(texto_metadados)

    if len(tokens_arquivo & tokens_metadados) >= _MIN_PALAVRAS_EM_COMUM:
        return None

    return (
        "o título/autor extraído tem pouca ou nenhuma palavra em comum com o "
        "nome do arquivo original — confira se os metadados são mesmo deste "
        "documento (o modelo pode ter catalogado outra obra citada no texto)"
    )
