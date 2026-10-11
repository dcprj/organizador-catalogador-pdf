from __future__ import annotations

from pathlib import Path

import pytest

from organizador_pdf import estado as estado_mod
from organizador_pdf.estado import EstadoDeExecucao, ParametrosSalvos


@pytest.fixture(autouse=True)
def isolar_caminho_estado(tmp_path: Path, monkeypatch):
    """Nunca deixa os testes tocarem o estado real do usuário (~/.organizador-pdf)."""
    monkeypatch.setattr(estado_mod, "CAMINHO_ESTADO", tmp_path / "estado.json")


def _parametros(**sobrescritas) -> ParametrosSalvos:
    base = dict(
        origem="/origem",
        destino="/destino",
        dry_run=False,
        recursive=True,
        mover=False,
        subpasta_markdown=None,
        paralelo=1,
        quarantine=True,
        enriquecimento_online=True,
        estrutura="cnpq",
        max_paginas=10,
        max_caracteres=30_000,
    )
    base.update(sobrescritas)
    return ParametrosSalvos(**base)


class TestCarregar:
    def test_sem_arquivo_devolve_none(self):
        assert EstadoDeExecucao.carregar() is None

    def test_arquivo_corrompido_devolve_none(self):
        estado_mod.CAMINHO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        estado_mod.CAMINHO_ESTADO.write_text("{ isso não é json válido", encoding="utf-8")
        assert EstadoDeExecucao.carregar() is None

    def test_ignora_campos_legados_ao_carregar(self):
        estado_mod.CAMINHO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        conteudo_antigo = {
            "criado_em": "2025-01-01T00:00:00",
            "parametros": {
                "origem": "/antigo/in",
                "destino": "/antigo/out",
                "modelo": "qwen2.5:3b-instruct",
                "provedor": "ollama",
                "ollama_url": "http://localhost:11434",
                "provedor_fallback": None,
                "modelo_fallback": None,
            },
            "concluidos": [],
        }
        import json
        estado_mod.CAMINHO_ESTADO.write_text(json.dumps(conteudo_antigo), encoding="utf-8")
        recarregado = EstadoDeExecucao.carregar()
        assert recarregado is not None
        assert recarregado.parametros.origem == "/antigo/in"
        assert recarregado.parametros.destino == "/antigo/out"


class TestSalvarERecarregar:
    def test_round_trip_preserva_parametros_e_concluidos(self, tmp_path: Path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        original = EstadoDeExecucao(parametros=_parametros(max_paginas=5))
        original.marcar_concluido(pdf)

        recarregado = EstadoDeExecucao.carregar()
        assert recarregado is not None
        assert recarregado.parametros == original.parametros
        assert recarregado.concluidos == {str(pdf.resolve())}

    def test_nunca_persiste_api_key(self):
        # ParametrosSalvos não tem sequer o campo — é uma garantia estrutural,
        # não de runtime, mas o teste documenta a intenção.
        assert not hasattr(_parametros(), "api_key")
        assert not hasattr(_parametros(), "api_key_fallback")


class TestMarcarConcluido:
    def test_persiste_incrementalmente(self, tmp_path: Path):
        pdf1 = tmp_path / "a.pdf"
        pdf2 = tmp_path / "b.pdf"
        e = EstadoDeExecucao(parametros=_parametros())

        e.marcar_concluido(pdf1)
        assert EstadoDeExecucao.carregar().concluidos == {str(pdf1.resolve())}

        e.marcar_concluido(pdf2)
        assert EstadoDeExecucao.carregar().concluidos == {
            str(pdf1.resolve()),
            str(pdf2.resolve()),
        }

    def test_sucessos_e_falhas_separados(self, tmp_path: Path):
        pdf1 = tmp_path / "ok.pdf"
        pdf2 = tmp_path / "falha.pdf"
        e = EstadoDeExecucao(parametros=_parametros())

        e.marcar_sucesso(pdf1)
        e.marcar_falha(pdf2, "conexão recusada")

        recarregado = EstadoDeExecucao.carregar()
        assert recarregado is not None
        assert str(pdf1.resolve()) in recarregado.sucessos
        assert str(pdf2.resolve()) not in recarregado.sucessos
        assert recarregado.falhas[str(pdf2.resolve())] == "conexão recusada"

        # Recuperação posterior da falha
        e.marcar_sucesso(pdf2)
        recarregado2 = EstadoDeExecucao.carregar()
        assert str(pdf2.resolve()) in recarregado2.sucessos
        assert str(pdf2.resolve()) not in recarregado2.falhas


class TestLimpar:
    def test_remove_o_arquivo(self, tmp_path: Path):
        pdf = tmp_path / "a.pdf"
        EstadoDeExecucao(parametros=_parametros()).marcar_concluido(pdf)
        assert estado_mod.CAMINHO_ESTADO.exists()

        EstadoDeExecucao.limpar()
        assert not estado_mod.CAMINHO_ESTADO.exists()
        assert EstadoDeExecucao.carregar() is None

    def test_idempotente_sem_arquivo(self):
        EstadoDeExecucao.limpar()  # não deve levantar exceção
