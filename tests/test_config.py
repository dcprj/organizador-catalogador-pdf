"""Testes de configuração do organizador_pdf."""

from __future__ import annotations

import warnings
from pathlib import Path
import pytest

from organizador_pdf.config import Config, ErroDeConfiguracao


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch, tmp_path):
    for variavel in (
        "ORGPDF_MAX_PAGINAS",
        "ORGPDF_MAX_CARACTERES",
        "ORGPDF_VERIFICAR_ONLINE",
        "TYPESAFE_API_KEY",
        "ORGPDF_TYPESAFE_API_KEY",
        "ORGPDF_PROVEDOR",
        "ORGPDF_MODELO",
        "ORGPDF_OLLAMA_URL",
        "ORGPDF_API_KEY",
        "ORGPDF_ANTHROPIC_API_KEY",
        "ORGPDF_OPENAI_API_KEY",
        "ORGPDF_DEEPSEEK_API_KEY",
        "ORGPDF_GEMINI_API_KEY",
        "ORGPDF_GROK_API_KEY",
        "ORGPDF_PROVEDOR_FALLBACK",
        "ORGPDF_MODELO_FALLBACK",
        "CLASSIFICADOR",
        "ORGPDF_CLASSIFICADOR",
    ):
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.chdir(tmp_path)


class TestValoresPadraoEPrecedencia:
    def test_valores_padrao(self):
        config = Config.do_ambiente()
        assert config.max_paginas == 10
        assert config.max_caracteres == 30_000
        assert config.verificar_online is True
        assert config.cache_bibliografico is True
        assert config.typesafe_api_key is None
        assert config.classificador == "jev_remoto"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"
        # Sem chave de API, a validação de pré-execução para modo remoto dispara ErroDeConfiguracao
        with pytest.raises(ErroDeConfiguracao, match="TYPESAFE_API_KEY"):
            config.validar()

    def test_max_paginas_via_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "8")
        assert Config.do_ambiente().max_paginas == 8

    def test_max_paginas_via_cli_sobrescreve_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "10")
        assert Config.do_ambiente(max_paginas=5).max_paginas == 5

    def test_max_paginas_invalido_gera_erro(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "dez")
        with pytest.raises(ErroDeConfiguracao, match="número inteiro"):
            Config.do_ambiente()

    def test_max_paginas_nao_positivo_gera_erro(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "0")
        with pytest.raises(ErroDeConfiguracao, match="maior que zero"):
            Config.do_ambiente()

    def test_max_caracteres_via_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "15000")
        assert Config.do_ambiente().max_caracteres == 15000

    def test_max_caracteres_via_cli_sobrescreve_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "15000")
        assert Config.do_ambiente(max_caracteres=5000).max_caracteres == 5000

    def test_max_caracteres_invalido_gera_erro(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "mil")
        with pytest.raises(ErroDeConfiguracao, match="número inteiro"):
            Config.do_ambiente()

    def test_max_caracteres_nao_positivo_gera_erro(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "-10")
        with pytest.raises(ErroDeConfiguracao, match="maior que zero"):
            Config.do_ambiente()

    def test_verificar_online_via_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "false")
        assert Config.do_ambiente().verificar_online is False

        monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "0")
        assert Config.do_ambiente().verificar_online is False

        monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "sim")
        assert Config.do_ambiente().verificar_online is True

    def test_verificar_online_invalido_gera_erro(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "talvez")
        with pytest.raises(ErroDeConfiguracao, match="booleano"):
            Config.do_ambiente()

    def test_verificar_online_via_cli_sobrescreve_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "true")
        assert Config.do_ambiente(verificar_online=False).verificar_online is False


class TestTypeSafeIntegracao:
    def test_typesafe_key_via_ambiente(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key-123")
        config = Config.do_ambiente()
        assert config.typesafe_api_key == "ts-test-key-123"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"

    def test_typesafe_key_via_orgpdf_prefixo(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_TYPESAFE_API_KEY", "ts-test-key-456")
        config = Config.do_ambiente()
        assert config.typesafe_api_key == "ts-test-key-456"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"

    def test_typesafe_key_via_cli(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-env-key")
        config = Config.do_ambiente(typesafe_api_key="ts-cli-key")
        assert config.typesafe_api_key == "ts-cli-key"
        assert config.modelo == "jev-typesafe"


class TestCompatibilidadeLegada:
    def test_variaveis_antigas_de_provedores_sao_ignoradas(self, monkeypatch):
        # Nenhuma variável legada altera o classificador atual nem causa erro
        monkeypatch.setenv("ORGPDF_PROVEDOR", "openai")
        monkeypatch.setenv("ORGPDF_MODELO", "gpt-4o")
        monkeypatch.setenv("ORGPDF_OLLAMA_URL", "http://localhost:11434")
        monkeypatch.setenv("ORGPDF_OPENAI_API_KEY", "sk-proj-xyz")
        monkeypatch.setenv("ORGPDF_ANTHROPIC_API_KEY", "sk-ant-xyz")

        config = Config.do_ambiente()
        assert config.classificador == "jev_remoto"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"

    def test_kwargs_legados_emitem_deprecation_warning(self):
        with pytest.deprecated_call():
            config = Config.do_ambiente(provedor="anthropic", modelo="claude-3")
        assert config.classificador == "jev_remoto"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"


class TestUniformidadeRemotoEScenarios:
    def test_modo_local_sem_chave_valida_com_sucesso(self):
        config = Config.do_ambiente(classificador="local")
        assert config.classificador == "local"
        assert config.modelo == "jev-system-one-local"
        assert config.provedor == "deterministico_local"
        # Modo local não requer chave e valida sem erro
        config.validar()

    def test_modo_remoto_com_chave_valida_com_sucesso(self):
        config = Config.do_ambiente(typesafe_api_key="ts-chave-valida")
        assert config.classificador == "jev_remoto"
        assert config.typesafe_api_key == "ts-chave-valida"
        assert config.modelo == "jev-typesafe"
        assert config.provedor == "typesafe"
        config.validar()

    def test_classificador_jev_sem_chave_dispara_erro(self):
        from organizador_pdf.classifier_jev import JevClassifier
        with pytest.raises(ErroDeConfiguracao, match="TYPESAFE_API_KEY"):
            JevClassifier(api_key=None, modo="jev_remoto")

    def test_classificador_jev_sdk_ausente_dispara_erro_remoto(self, monkeypatch):
        import builtins
        from organizador_pdf.classifier_jev import JevClassifier, ErroDeClassificacaoRemota

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("typesafe_sdk"):
                raise ImportError("No module named 'typesafe_sdk'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ErroDeClassificacaoRemota, match="typesafe-sdk"):
            classifier = JevClassifier(api_key="ts-test-key", modo="jev_remoto")
            classifier.classify_and_validate(pdf_path="teste.pdf", texto_pre_extraido="Amostra de texto para teste")
