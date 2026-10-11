import hashlib
import logging
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .abnt_formatter import ABNTFormatter
from .classifier_jev import JevClassifier
from .config import Config
from .converter import DocumentoConvertido, ErroDeConversao, ErroPdfEscaneado, converter_pdf
from .extractor import ErroDeExtracao, ErroFatalDeAPI
from .metadata_api import MetadataEnricher
from .models import Metadados, ResultadoDoArquivo, Situacao, normalizar_isbn_canonico
from .organizer import (
    PASTA_REVISAO_MANUAL,
    ErroDeOrganizacao,
    gerar_markdown,
    montar_diretorio,
    organizar,
)
from .verificacao import verificar_identificadores

logger = logging.getLogger(__name__)


def _calcular_sha256(caminho: Path) -> str:
    """Calcula o hash SHA-256 do arquivo em disco."""
    h = hashlib.sha256()
    try:
        with open(caminho, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
    except Exception as exc:
        logger.debug("Erro ao calcular SHA-256 de %s: %s", caminho, exc)
        return ""
    return h.hexdigest()

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
    enriquecimento_online: bool = True
    estrutura: str = "cnpq"

    @property
    def online(self) -> bool:
        return self.enriquecimento_online


class ExtratorDeterministico:
    """Extrator padrão que roda localmente usando Jev/CIP e APIs públicas."""

    def __init__(
        self,
        enriquecimento_online: bool = True,
        max_paginas: int = 10,
        max_caracteres: int = 30000,
        typesafe_api_key: Optional[str] = None,
        classificador_modo: str = "auto",
    ) -> None:
        self.classifier = JevClassifier(api_key=typesafe_api_key, modo=classificador_modo)
        self.enricher = MetadataEnricher(online=enriquecimento_online)
        self.max_paginas = max_paginas
        self.max_caracteres = max_caracteres

    def extrair(self, documento: DocumentoConvertido) -> Metadados:
        jev_res = self.classifier.classify_and_validate(
            str(documento.caminho),
            max_paginas=self.max_paginas,
            max_caracteres=self.max_caracteres,
            texto_pre_extraido=documento.markdown_inicial,
            total_paginas=documento.total_paginas,
            metadados_embutidos=documento.metadados_embutidos,
        )
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
        self.config = config or Config.do_ambiente()
        self.opcoes = opcoes or OpcoesDoPipeline(destino=Path("destino"))
        enriquecer = getattr(self.opcoes, "enriquecimento_online", getattr(self.opcoes, "online", True))
        enriquecer = enriquecer and getattr(self.config, "verificar_online", True)
        self.extrator = extrator or ExtratorDeterministico(
            enriquecimento_online=enriquecer,
            max_paginas=getattr(self.config, "max_paginas", 10),
            max_caracteres=getattr(self.config, "max_caracteres", 30000),
            typesafe_api_key=getattr(self.config, "typesafe_api_key", None),
            classificador_modo=getattr(self.config, "classificador", "auto"),
        )
        self.extrator_fallback = extrator_fallback
        self._isbns_lock = threading.Lock()
        self.isbns_vistos: dict[str, list[dict[str, Any]]] = {}

    def processar_arquivo(self, caminho: Path) -> ResultadoDoArquivo:
        """Processa um PDF, devolvendo o erro no resultado em vez de propagá-lo."""
        etapa = "conversão"
        try:
            documento = converter_pdf(
                caminho,
                paginas_inicio=getattr(self.config, "max_paginas", 10),
                paginas_fim=getattr(self.config, "max_paginas", 10),
                max_caracteres_analise=getattr(self.config, "max_caracteres", 30000),
            )

            etapa = "extração de metadados"
            metadados, aviso, usou_fallback = self._extrair(documento, caminho.name)

            enriquecer = getattr(self.opcoes, "enriquecimento_online", getattr(self.opcoes, "online", True))
            if enriquecer and getattr(self.config, "verificar_online", True):
                metadados, aviso_online = verificar_identificadores(metadados)
                if aviso_online:
                    aviso = f"{aviso} Além disso, {aviso_online}" if aviso else aviso_online

            raw_isbn = getattr(metadados.identificadores, "isbn", None)
            isbn_canonico = normalizar_isbn_canonico(raw_isbn) if raw_isbn else None
            sha256_atual = _calcular_sha256(caminho) if isbn_canonico else ""
            registro_lote = None

            if isbn_canonico:
                with self._isbns_lock:
                    if isbn_canonico in self.isbns_vistos and len(self.isbns_vistos[isbn_canonico]) > 0:
                        anteriores = self.isbns_vistos[isbn_canonico]
                        mesmo_conteudo = any(e.get("sha256") == sha256_atual for e in anteriores)
                        nomes_outros = ", ".join(f"'{e['caminho'].name}'" for e in anteriores)
                        if mesmo_conteudo:
                            msg_dup = (
                                f"ISBN repetido no lote (arquivo duplicado idêntico): ISBN '{isbn_canonico}' "
                                f"compartilhado com {nomes_outros} (mesmo conteúdo SHA-256)."
                            )
                        else:
                            msg_dup = (
                                f"ISBN repetido no lote (arquivos diferentes): ISBN '{isbn_canonico}' "
                                f"compartilhado com {nomes_outros} (conferir edições/volumes)."
                            )
                        metadados.needs_review = True
                        if msg_dup not in metadados.review_reasons:
                            metadados.review_reasons.append(msg_dup)
                        aviso = f"{aviso} | {msg_dup}" if aviso else msg_dup

                        # Tratamento simétrico para os arquivos já vistos no mesmo grupo (CA-04)
                        for e in anteriores:
                            e["duplicado"] = True
                            e_meta = e.get("metadados")
                            if e_meta:
                                e_meta.needs_review = True
                                msg_ant = (
                                    f"ISBN repetido no lote (arquivo duplicado idêntico): ISBN '{isbn_canonico}' "
                                    f"compartilhado com '{caminho.name}' (mesmo conteúdo SHA-256)."
                                    if e.get("sha256") == sha256_atual else
                                    f"ISBN repetido no lote (arquivos diferentes): ISBN '{isbn_canonico}' "
                                    f"compartilhado com '{caminho.name}' (conferir edições/volumes)."
                                )
                                if msg_ant not in e_meta.review_reasons:
                                    e_meta.review_reasons.append(msg_ant)
                    else:
                        self.isbns_vistos[isbn_canonico] = []

                    registro_lote = {
                        "caminho": caminho,
                        "sha256": sha256_atual,
                        "metadados": metadados,
                        "duplicado": isbn_canonico in self.isbns_vistos and len(self.isbns_vistos[isbn_canonico]) > 0,
                    }
                    self.isbns_vistos[isbn_canonico].append(registro_lote)

            if aviso:
                metadados.needs_review = True
                if aviso not in metadados.review_reasons:
                    metadados.review_reasons.append(aviso)

            prov_classificador = getattr(metadados, "provedor_classificador", None)
            if prov_classificador:
                provedor_usado = prov_classificador
            else:
                provedor_usado = self._nome_do_provedor_usado(usou_fallback)

            if metadados.needs_review and metadados.review_reasons:
                revisoes_str = "; ".join(metadados.review_reasons)
                aviso = f"{aviso} | {revisoes_str}" if aviso else revisoes_str

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
                estrutura=getattr(self.opcoes, "estrutura", "cnpq"),
            )

            res_obj = ResultadoDoArquivo(
                origem=caminho,
                situacao=Situacao.SIMULADO if resultado.simulado else Situacao.SUCESSO,
                metadados=metadados,
                pdf_destino=resultado.pdf_destino,
                markdown_destino=resultado.markdown_destino,
                aviso=aviso,
                provedor_usado=provedor_usado,
                usou_fallback=usou_fallback,
            )

            if registro_lote is not None:
                with self._isbns_lock:
                    registro_lote["pdf_destino"] = resultado.pdf_destino
                    registro_lote["markdown_destino"] = resultado.markdown_destino
                    registro_lote["resultado_ref"] = res_obj

            return res_obj

        except ErroFatalDeAPI:
            raise
        except ErroPdfEscaneado as exc:
            logger.info("[%s] PDF digitalizado sem camada de texto nativa: %s", caminho.name, exc)
            return ResultadoDoArquivo(
                origem=caminho,
                situacao=Situacao.PENDENTE_OCR,
                aviso="PDF digitalizado sem camada de texto nativa (requer OCR prévio)",
                erro=str(exc),
                etapa="conversão",
            )
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
        with self._isbns_lock:
            self.isbns_vistos.clear()
        resultados: list[ResultadoDoArquivo] = []
        for caminho in caminhos:
            resultado = self.processar_arquivo(caminho)
            resultados.append(resultado)
            if ao_concluir:
                ao_concluir(resultado)
        self.consolidar_duplicatas(resultados)
        return resultados

    def consolidar_duplicatas(self, resultados: list[ResultadoDoArquivo]) -> None:
        """Consolida e trata simetricamente todos os arquivos com identificador duplicado no lote (CA-04)."""
        with self._isbns_lock:
            for isbn_canonico, registros in self.isbns_vistos.items():
                if len(registros) <= 1:
                    continue

                # Verifica se todos os registros referem-se à mesma edição da obra
                titulos_validos = [
                    re.sub(r"[^\w\s]", "", (r.get("metadados").titulo or "").lower()).strip()
                    for r in registros
                    if r.get("metadados") and r.get("metadados").titulo
                ]
                edicoes_validas = [
                    str(getattr(r.get("metadados"), "edition", "") or getattr(r.get("metadados"), "edicao", "") or "").lower().strip()
                    for r in registros
                    if r.get("metadados")
                ]
                mesma_edicao = (
                    len(set(titulos_validos)) <= 1
                    and len(set(edicoes_validas)) <= 1
                    and len(registros) >= 2
                )

                if mesma_edicao:
                    for reg in registros:
                        meta = reg.get("metadados")
                        if not meta:
                            continue
                        meta.needs_review = False
                        aviso_mesma_ed = (
                            f"ISBN repetido no lote para múltiplos arquivos referentes à mesma edição da obra ('{meta.titulo}')."
                        )
                        if aviso_mesma_ed not in meta.avisos:
                            meta.avisos.append(aviso_mesma_ed)
                    continue

                for reg in registros:
                    meta = reg.get("metadados")
                    if not meta:
                        continue
                    meta.needs_review = True

                    # Se a quarentena estiver ativada e não for dry-run:
                    # Garantir que PDF e Markdown fiquem em revisao_manual
                    pdf_dst = reg.get("pdf_destino")
                    md_dst = reg.get("markdown_destino")
                    if (
                        self.opcoes.quarantine
                        and not self.opcoes.dry_run
                        and pdf_dst
                        and pdf_dst.exists()
                        and PASTA_REVISAO_MANUAL not in pdf_dst.parts
                    ):
                        try:
                            dir_pdf_rev, dir_md_rev = montar_diretorio(
                                self.opcoes.destino,
                                meta,
                                subpasta_markdown=self.opcoes.subpasta_markdown,
                                revisao_manual=True,
                                estrutura=getattr(self.opcoes, "estrutura", "cnpq"),
                            )
                            dir_pdf_rev.mkdir(parents=True, exist_ok=True)
                            dir_md_rev.mkdir(parents=True, exist_ok=True)

                            novo_pdf = dir_pdf_rev / pdf_dst.name
                            novo_md = dir_md_rev / md_dst.name if md_dst else None

                            # Reloca e atualiza arquivo de auditoria se existir
                            audit_src = pdf_dst.parent / f"{pdf_dst.stem}.auditoria.json"
                            novo_audit = dir_pdf_rev / f"{novo_pdf.stem}.auditoria.json"
                            if audit_src.exists() and audit_src != novo_audit:
                                try:
                                    import json
                                    dados_aud = json.loads(audit_src.read_text(encoding="utf-8"))
                                    dados_aud["decisao_encaminhamento"] = "revisao_manual"
                                    dados_aud["caminho_final"] = str(novo_pdf)
                                    novo_audit.write_text(
                                        json.dumps(dados_aud, indent=2, ensure_ascii=False),
                                        encoding="utf-8",
                                    )
                                    audit_src.unlink(missing_ok=True)
                                except Exception as e_aud:
                                    logger.warning("Falha ao relocar auditoria de '%s': %s", audit_src, e_aud)

                            if novo_pdf != pdf_dst:
                                shutil.move(str(pdf_dst), str(novo_pdf))
                                reg["pdf_destino"] = novo_pdf
                                res_ref = reg.get("resultado_ref")
                                if res_ref:
                                    res_ref.pdf_destino = novo_pdf

                            if md_dst and md_dst.exists() and novo_md and novo_md != md_dst:
                                shutil.move(str(md_dst), str(novo_md))
                                reg["markdown_destino"] = novo_md
                                res_ref = reg.get("resultado_ref")
                                if res_ref:
                                    res_ref.markdown_destino = novo_md

                            if meta.registro_rastreabilidade:
                                meta.registro_rastreabilidade.decisao_encaminhamento = "revisao_manual"
                                meta.registro_rastreabilidade.caminho_final = str(novo_pdf)

                            if novo_md and novo_md.exists():
                                doc_conteudo = ""
                                try:
                                    texto_antigo = novo_md.read_text(encoding="utf-8")
                                    if "## Conteúdo" in texto_antigo:
                                        doc_conteudo = texto_antigo.split("## Conteúdo", 1)[1].lstrip("\n- \r")
                                except Exception:
                                    pass

                                novo_md_texto = gerar_markdown(
                                    meta,
                                    doc_conteudo,
                                    arquivo_origem=reg.get("caminho"),
                                    total_paginas=meta.total_pages,
                                    provedor_extracao=getattr(meta, "provedor_classificador", None),
                                )
                                novo_md.write_text(novo_md_texto, encoding="utf-8")
                        except Exception as exc:
                            logger.warning("Falha ao relocar duplicata '%s' para revisão manual: %s", reg["caminho"].name, exc)

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
            return getattr(self.config, "provedor", "deterministico_local")
        return "fallback"

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


def _e_nome_arquivo_informativo(stem: str) -> bool:
    """Verifica se o nome do arquivo traz pistas bibliográficas úteis (não é apenas 'scan_001', 'documento', etc.)."""
    stem_clean = re.sub(r"[0-9_ -]+", "", stem.lower())
    if len(stem_clean) < 4 or stem_clean in (
        "scan", "doc", "documento", "pdf", "file", "arquivo", "download",
        "print", "livro", "artigo", "teste", "novo", "output"
    ):
        return False
    tokens = _tokenizar(stem)
    return len(tokens) >= 2


def _titulo_diverge_do_arquivo(nome_arquivo: str, metadados: Metadados) -> Optional[str]:
    """Heurística para alertar se o título/autor diverge do nome do arquivo original.

    Desacopla a checagem de título e autor: a coincidência de sobrenomes de autores
    não atenua nem cancela a divergência constatada no título (CA-01 / CA-05).
    """
    stem = Path(nome_arquivo).stem
    if not _e_nome_arquivo_informativo(stem):
        return None

    tokens_arquivo = _tokenizar(stem)
    if len(tokens_arquivo) < _MIN_PALAVRAS_EM_COMUM:
        return None

    # Tokens de título e subtítulo da obra
    tokens_obra = _tokenizar(f"{metadados.titulo or ''} {metadados.subtitulo or ''}")
    # Tokens de autoria
    tokens_autor = _tokenizar(f"{metadados.autor_principal or ''} {' '.join(metadados.autores or [])}")

    comum_obra = tokens_arquivo & tokens_obra
    comum_autor = tokens_arquivo & tokens_autor

    # Se a obra possui pelo menos _MIN_PALAVRAS_EM_COMUM em comum com o arquivo, está alinhada
    if len(comum_obra) >= _MIN_PALAVRAS_EM_COMUM:
        return None

    razao_obra = len(comum_obra) / len(tokens_arquivo) if tokens_arquivo else 0.0

    # CASO CRÍTICO (CA-01 / CA-05): Título tem 0 palavras em comum com arquivo informativo
    # Mesmo que o autor coincida no nome do arquivo, a divergência de título é mandatória
    if len(comum_obra) == 0:
        return (
            f"o título extraído ('{metadados.titulo}') não possui nenhuma palavra significativa em comum "
            f"com o nome do arquivo original ('{nome_arquivo}') — verifique se os metadados são mesmo deste "
            "documento ou se representam aviso de copyright ou outra obra citada"
        )

    # Se há apenas 1 palavra em comum e a sobreposição é baixa (< 20%)
    if len(comum_obra) < _MIN_PALAVRAS_EM_COMUM and razao_obra < 0.20:
        if not comum_autor:
            return (
                "o título e autor extraídos têm pouca ou nenhuma palavra em comum com o "
                "nome do arquivo original — confira se os metadados são mesmo deste "
                "documento (o modelo pode ter catalogado outra obra citada no texto)"
            )
        return (
            f"o título extraído ('{metadados.titulo}') tem sobreposição lexical muito baixa ({razao_obra:.0%}) "
            f"com o nome do arquivo ('{nome_arquivo}') — confira a exatidão do título"
        )

    return None


OrganizadorPipeline = Pipeline
