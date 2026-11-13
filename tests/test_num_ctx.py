"""Ollama's context size (num_ctx) from the terminal: the setting, --num-ctx and /num-ctx."""

import pytest

from labb import llm, model
from labb.cli import app


@pytest.fixture(autouse=True)
def reset():
    yield
    llm.set_num_ctx(None)


def test_set_num_ctx_makes_the_models_again():
    before = model.model_with_tools(llm.default.spec, False)
    llm.set_num_ctx(65536)
    assert llm.ollama_num_ctx == 65536 == llm.default.model.num_ctx == llm.load("ollama:other").model.num_ctx
    # The tools were bound to the old model
    assert model.model_with_tools(llm.default.spec, False) is not before
    llm.set_num_ctx(None)
    assert llm.ollama_num_ctx == llm.ENV_NUM_CTX == llm.default.model.num_ctx


def test_sizes_out_of_range_are_refused():
    for bad in (0, 100, 10**9):
        with pytest.raises(ValueError):
            llm.set_num_ctx(bad)
    assert llm.ollama_num_ctx == llm.ENV_NUM_CTX


def test_tokens_are_read_with_k_and_m():
    assert app.parse_tokens("65536") == 65536 == app.parse_tokens("64k") == app.parse_tokens("64K")
    assert app.parse_tokens("1m") == 1_048_576 and app.parse_tokens("32,768") == 32768
    for bad in ("", "lots", "k", "1.5k"):
        with pytest.raises(ValueError):
            app.parse_tokens(bad)


def test_the_setting_applies(tmp_path, monkeypatch):
    (tmp_path / ".labb").mkdir()
    (tmp_path / ".labb" / "settings.json").write_text('{"ollamaNumCtx": 16384}')
    monkeypatch.setattr(app.settings, "user_file", lambda: tmp_path / "none.json")
    app.apply_settings(str(tmp_path), None, app.console)
    assert llm.ollama_num_ctx == 16384


def test_a_bad_setting_keeps_the_env_size(tmp_path, monkeypatch, capsys):
    (tmp_path / ".labb").mkdir()
    (tmp_path / ".labb" / "settings.json").write_text('{"ollamaNumCtx": 5}')
    monkeypatch.setattr(app.settings, "user_file", lambda: tmp_path / "none.json")
    app.apply_settings(str(tmp_path), None, app.console)
    assert llm.ollama_num_ctx == llm.ENV_NUM_CTX
