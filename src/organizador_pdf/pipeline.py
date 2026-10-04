"""Pipeline de processamento por arquivo, resiliente a falhas individuais."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .abnt_formatter import ABNTFormatter
from .classifier_jev import JevClassifier
from .config import Config
from .converter import DocumentoConvertido, ErroDeConversao, converter_pdf
from .extractor import ErroDeExtracao, ErroFatalDeAPI
from .metadata_api import MetadataEnricher
from .models import Metadados, ResultadoDoArquivo, Situacao
from .organizer import ErroDeOrganizacao, gerar_markdown, organizar
from .verificacao import verificar_identificadores

logger = logging.getLogger(__name__)

#: Palavras comuns demais para servir de sinal de identidade do documento.
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

_MIN_PALAVRAS_EM_COMUM = 2


@dataclass
class OpcoesDoPipeline:
    """Parâmetros de execução escolhidos na linha de comando."""

    destino: Path
    dry_run: bool = False
    mover: bool = False
    subpasta_markdown: Optional[str] = None
    quarantine: bool = True
    online: bool = True


class ExtratorDeterministico:
    """Extrator padrão que roda localmente usando Jev/CIP e APIs públicas."""

    def __init__(self, online: bool = True) -> None:
        self.classifier = JevClassifier()
        self.enricher = MetadataEnricher(online=online)

    def extrair(self, documento: DocumentoConvertido) -> Metadados:
        jev_res = self.classifier.classify_and_validate(str(documento.caminho))
        meta = self.enricher.enrich(jev_res)
        abnt_ref = ABNTFormatter.format(meta)
        meta.referencia_abnt = abnt_ref
        return meta


class Pipeline:
    """Executa converter → extrair → gerar Markdown → organizar."""

    def __init__(
        self,
        config: Optional[Config] = None,
        opcoes: Optional[OpcoesDoPipeline] = None,
        extrator: Optional[Any] = None,
        extrator_fallback: Optional[Any] = None,
    ) -> None:
        self.config = config or Config()
        self.opcoes = opcoes or OpcoesDoPipeline(destino=Path("destino"))
        online = getattr(self.opcoes, "online", True)
        self.extrator = extrator or ExtratorDeterministico(online=online)
        self.extrator_fallback = extrator_fallback

    def processar_arquivo(self, caminho: Path) -> ResultadoDoArquivo:
        """Processa um PDF, devolvendo o erro no resultado em vez de propagá-lo."""
        etapa = "conversão"
        try:
            documento = converter_pdf(
                caminho,
                paginas_inicio=getattr(self.config, "max_paginas", 10),
                max_caracteres_analise=getattr(self.config, "max_caracteres", 30000),
            )

            etapa = "extração de metadados"
            metadados, aviso, usou_fallback = self._extrair(documento, caminho.name)
            provedor_usado = self._nome_do_provedor_usado(usou_fallback)

            if getattr(self.opcoes, "online", True) and getattr(self.config, "verificar_online", True):
                metadados, aviso_online = verificar_identificadores(metadados)
                if aviso_online:
                    aviso = f"{aviso} Além disso, {aviso_online}" if aviso else aviso_online

            if aviso:
                logger.warning("[%s] %s", caminho.name, aviso)

            etapa = "geração do Markdown"
            markdown = self._montar_markdown(
                metadados, documento, provedor_usado=provedor_usado, usou_fallback=usou_fallback
            )

            etapa = "organização"
            revisao_manual = bool(aviso or metadados.needs_review) if self.opcoes.quarantine else False
            resultado = organizar(
                metadados,
                pdf_origem=caminho,
                destino=self.opcoes.destino,
                markdown=markdown,
                subpasta_markdown=self.opcoes.subpasta_markdown,
                mover=self.opcoes.mover,
                dry_run=self.opcoes.dry_run,
                revisao_manual=revisao_manual,
            )

            return ResultadoDoArquivo(
                origem=caminho,
                situacao=Situacao.SIMULADO if resultado.simulado else Situacao.SUCESSO,
                metadados=metadados,
                pdf_destino=resultado.pdf_destino,
                markdown_destino=resultado.markdown_destino,
                aviso=aviso,
                provedor_usado=provedor_usado,
                usou_fallback=usou_fallback,
            )

        except ErroFatalDeAPI:
            raise
        except (ErroDeConversao, ErroDeExtracao, ErroDeOrganizacao) as exc:
            return self._falha(caminho, etapa, str(exc))
        except Exception as exc:
            logger.debug("Erro inesperado em %s", caminho, exc_info=True)
            return self._falha(caminho, etapa, f"{type(exc).__name__}: {exc}")

    def processar_lote(
        self,
        caminhos: Iterable[Path],
        *,
        ao_concluir: Optional[Callable[[ResultadoDoArquivo], None]] = None,
    ) -> list[ResultadoDoArquivo]:
        """Processa vários PDFs em sequência."""
        resultados: list[ResultadoDoArquivo] = []
        for caminho in caminhos:
            resultado = self.processar_arquivo(caminho)
            resultados.append(resultado)
            if ao_concluir:
                ao_concluir(resultado)
        return resultados

    def _extrair(
        self, documento: DocumentoConvertido, nome_arquivo: str
    ) -> tuple[Metadados, Optional[str], bool]:
        """Extrai os metadados do documento."""
        try:
            metadados = self.extrator.extrair(documento)
        except ErroDeExtracao as exc:
            if self.extrator_fallback is None:
                raise
            logger.info("[%s] extração falhou (%s); tentando fallback.", nome_arquivo, exc)
            try:
                metadados = self.extrator_fallback.extrair(documento)
            except ErroDeExtracao as exc_fallback:
                raise ErroDeExtracao(f"local: {exc}; fallback: {exc_fallback}") from exc_fallback
            return metadados, _titulo_diverge_do_arquivo(nome_arquivo, metadados), True

        aviso = _titulo_diverge_do_arquivo(nome_arquivo, metadados)
        if aviso is None or self.extrator_fallback is None:
            return metadados, aviso, False

        logger.info("[%s] %s — tentando fallback.", nome_arquivo, aviso)
        try:
            metadados_fallback = self.extrator_fallback.extrair(documento)
        except ErroDeExtracao as exc:
            logger.warning("[%s] fallback também falhou (%s); mantendo resultado local.", nome_arquivo, exc)
            return metadados, aviso, False

        return metadados_fallback, _titulo_diverge_do_arquivo(nome_arquivo, metadados_fallback), True

    def _nome_do_provedor_usado(self, usou_fallback: bool) -> str:
        if not usou_fallback:
            prov = getattr(self.config, "provedor", None)
            return getattr(prov, "value", str(prov)) if prov else "deterministico_local"
        prov_fb = getattr(self.config, "provedor_fallback", None)
        return getattr(prov_fb, "value", str(prov_fb)) if prov_fb else "fallback"

    def _montar_markdown(
        self,
        metadados: Metadados,
        documento: DocumentoConvertido,
        *,
        provedor_usado: str,
        usou_fallback: bool,
    ) -> str:
        return gerar_markdown(
            metadados,
            documento.markdown_completo,
            arquivo_origem=documento.caminho,
            total_paginas=documento.total_paginas,
            provedor_extracao=provedor_usado,
            extraido_via_fallback=usou_fallback,
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
    """Heurística para alertar se o título/autor diverge do nome do arquivo."""
    tokens_arquivo = _tokenizar(Path(nome_arquivo).stem)
    if len(tokens_arquivo) < _MIN_PALAVRAS_EM_COMUM:
        return None

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
