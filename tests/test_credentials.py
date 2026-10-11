import pytest
from keyring.errors import KeyringError

from ha_rename import credentials

URL = "ws://ha.test/api/websocket"


def test_load_missing_token_returns_none():
    assert credentials.load_token(URL) is None


def test_save_then_load(memory_keyring):
    credentials.save_token(URL, "abc")
    assert credentials.load_token(URL) == "abc"
    assert memory_keyring.passwords == {("ha-rename", URL): "abc"}


def test_tokens_are_per_url():
    credentials.save_token(URL, "abc")
    assert credentials.load_token("ws://other.test/api/websocket") is None


@pytest.mark.parametrize("func, args", [
    (credentials.load_token, (URL,)),
    (credentials.save_token, (URL, "abc")),
])
def test_keyring_errors_become_credential_errors(monkeypatch, func, args):
    def boom(*a):
        raise KeyringError("locked")

    monkeypatch.setattr("keyring.get_password", boom)
    monkeypatch.setattr("keyring.set_password", boom)
    with pytest.raises(credentials.CredentialError, match="locked"):
        func(*args)


def test_prompt_hides_input_and_strips_whitespace(monkeypatch, capsys):
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return "  pasted-token\n"

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    assert credentials.prompt_token(URL) == "pasted-token"
    assert prompts == ["Home Assistant access token: "]
    err = capsys.readouterr().err
    assert URL in err
    assert "Long-lived access tokens" in err


def test_prompt_rejects_empty_token(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "   ")
    with pytest.raises(credentials.CredentialError, match="No token entered"):
        credentials.prompt_token(URL)
