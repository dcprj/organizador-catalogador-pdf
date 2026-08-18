from __future__ import annotations

import pytest

from organizador_pdf.config import (
    MODELO_PADRAO,
    OLLAMA_URL_PADRAO,
    Config,
    ErroDeConfiguracao,
    Provedor,
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch, tmp_path):
    for variavel in (
        "ORGPDF_MODELO",
        "ORGPDF_OLLAMA_URL",
        "ORGPDF_MAX_PAGINAS",
        "ORGPDF_MAX_CARACTERES",
        "ORGPDF_VERIFICAR_ONLINE",
        "ORGPDF_PROVEDOR",
        "ORGPDF_API_KEY",
        "ORGPDF_ANTHROPIC_API_KEY",
        "ORGPDF_OPENAI_API_KEY",
        "ORGPDF_DEEPSEEK_API_KEY",
        "ORGPDF_GEMINI_API_KEY",
        "ORGPDF_GROK_API_KEY",
    ):
        monkeypatch.delenv(variavel, raising=False)
    # Impede que um .env do repositório vaze para dentro dos testes.
    monkeypatch.chdir(tmp_path)


class TestPrecedencia:
    def test_padroes(self):
        config = Config.do_ambiente()
        assert config.modelo == MODELO_PADRAO == "qwen2.5:3b-instruct"
        assert config.ollama_url == OLLAMA_URL_PADRAO

    def test_ambiente_sobrescreve_padrao(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MODELO", "qwen2.5:7b-instruct")
        assert Config.do_ambiente().modelo == "qwen2.5:7b-instruct"

    def test_cli_sobrescreve_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MODELO", "qwen2.5:7b-instruct")

        config = Config.do_ambiente(modelo="llama3.2:3b")

        assert config.modelo == "llama3.2:3b"

    def test_url_do_ollama_configuravel(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_OLLAMA_URL", "http://192.168.0.10:11434")
        assert Config.do_ambiente().ollama_url == "http://192.168.0.10:11434"

    def test_url_do_ollama_via_cli(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_OLLAMA_URL", "http://192.168.0.10:11434")
        config = Config.do_ambiente(ollama_url="http://localhost:11434")
        assert config.ollama_url == "http://localhost:11434"

    def test_inteiro_invalido(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "zero")
        with pytest.raises(ErroDeConfiguracao, match="número inteiro"):
            Config.do_ambiente()

    def test_inteiro_nao_positivo(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "0")
        with pytest.raises(ErroDeConfiguracao, match="maior que zero"):
            Config.do_ambiente()


class TestProvedor:
    def test_padrao_e_ollama_sem_exigir_api_key(self):
        config = Config.do_ambiente()
        assert config.provedor is Provedor.OLLAMA
        assert config.api_key is None
        assert config.modelo == MODELO_PADRAO

    def test_provedor_via_cli(self, monkeypatch):
        config = Config.do_ambiente(
            provedor="anthropic", modelo="claude-haiku-4-5", api_key="sk-ant-teste"
        )
        assert config.provedor is Provedor.ANTHROPIC

    def test_provedor_via_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_PROVEDOR", "openai")
        monkeypatch.setenv("ORGPDF_MODELO", "gpt-5-mini")
        monkeypatch.setenv("ORGPDF_OPENAI_API_KEY", "sk-teste")
        config = Config.do_ambiente()
        assert config.provedor is Provedor.OPENAI

    def test_cli_sobrescreve_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_PROVEDOR", "openai")
        config = Config.do_ambiente(
            provedor="anthropic", modelo="claude-haiku-4-5", api_key="sk-ant-teste"
        )
        assert config.provedor is Provedor.ANTHROPIC

    def test_provedor_invalido_gera_erro(self):
        with pytest.raises(ErroDeConfiguracao, match="não reconhecido"):
            Config.do_ambiente(provedor="chatgpt-3.5-turbo-plus")

    def test_provedor_pago_sem_modelo_explicito_gera_erro(self):
        with pytest.raises(ErroDeConfiguracao, match="modelo explícito"):
            Config.do_ambiente(provedor="anthropic", api_key="sk-ant-teste")

    def test_provedor_pago_com_modelo_explicito_e_aceito(self):
        config = Config.do_ambiente(
            provedor="anthropic", modelo="claude-opus-5", api_key="sk-ant-teste"
        )
        assert config.modelo == "claude-opus-5"

    def test_provedor_pago_sem_api_key_gera_erro(self):
        with pytest.raises(ErroDeConfiguracao, match="chave de API"):
            Config.do_ambiente(provedor="anthropic", modelo="claude-haiku-4-5")

    def test_api_key_via_cli(self):
        config = Config.do_ambiente(
            provedor="anthropic", modelo="claude-haiku-4-5", api_key="sk-ant-cli"
        )
        assert config.api_key == "sk-ant-cli"

    def test_api_key_via_variavel_especifica_do_provedor(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_ANTHROPIC_API_KEY", "sk-ant-especifica")
        config = Config.do_ambiente(provedor="anthropic", modelo="claude-haiku-4-5")
        assert config.api_key == "sk-ant-especifica"

    def test_api_key_via_variavel_generica_de_fallback(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_API_KEY", "sk-generica")
        config = Config.do_ambiente(provedor="openai", modelo="gpt-5-mini")
        assert config.api_key == "sk-generica"

    def test_api_key_especifica_tem_precedencia_sobre_generica(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_API_KEY", "sk-generica")
        monkeypatch.setenv("ORGPDF_OPENAI_API_KEY", "sk-openai-especifica")
        config = Config.do_ambiente(provedor="openai", modelo="gpt-5-mini")
        assert config.api_key == "sk-openai-especifica"

    def test_api_key_cli_tem_precedencia_sobre_ambiente(self, monkeypatch):
        monkeypatch.setenv("ORGPDF_ANTHROPIC_API_KEY", "sk-ambiente")
        config = Config.do_ambiente(
            provedor="anthropic", modelo="claude-haiku-4-5", api_key="sk-cli"
        )
        assert config.api_key == "sk-cli"

    def test_ollama_ignora_api_key_ainda_que_definida(self, monkeypatch):
        # Chave de outro provedor sobrando no .env não deve vazar para o
        # Ollama nem exigir nada quando o provedor é o padrão local.
        monkeypatch.setenv("ORGPDF_ANTHROPIC_API_KEY", "sk-sobrando")
        config = Config.do_ambiente()
        assert config.provedor is Provedor.OLLAMA
        assert config.api_key is None
