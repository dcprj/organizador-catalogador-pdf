from __future__ import annotations

import pytest

from organizador_pdf.config import MODELO_PADRAO, OLLAMA_URL_PADRAO, Config, ErroDeConfiguracao


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch, tmp_path):
    for variavel in (
        "ORGPDF_MODELO",
        "ORGPDF_OLLAMA_URL",
        "ORGPDF_MAX_PAGINAS",
        "ORGPDF_MAX_CARACTERES",
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
