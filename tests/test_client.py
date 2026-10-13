import asyncio
import email.message
import io
import urllib.error
import urllib.request

import pytest

from ha_rename import client
from tests.conftest import GOOD_TOKEN, SAMPLE_ENTITIES


def run(coro):
    return asyncio.run(coro)


def test_list_entities(fake_ha):
    async def go():
        async with client.connect(fake_ha.url, GOOD_TOKEN) as ha:
            return await ha.list_entities()

    assert run(go()) == SAMPLE_ENTITIES


def test_call_uses_increasing_ids_and_skips_unrelated_messages(fake_ha):
    async def go():
        async with client.connect(fake_ha.url, GOOD_TOKEN) as ha:
            await ha.list_entities()
            for name in ("Lights", "Kitchen lights"):
                await ha.call(
                    {
                        "type": "config/entity_registry/update",
                        "entity_id": "light.kitchen",
                        "name": name,
                    }
                )

    run(go())
    assert [u["name"] for u in fake_ha.updates] == ["Lights", "Kitchen lights"]


def test_bad_token(fake_ha):
    async def go():
        async with client.connect(fake_ha.url, "wrong"):
            pass  # pragma: no cover

    with pytest.raises(client.AuthenticationError, match="Invalid access token"):
        run(go())


def test_unexpected_first_message(fake_ha):
    fake_ha.first_message = {"type": "hello"}

    async def go():
        async with client.connect(fake_ha.url, GOOD_TOKEN):
            pass  # pragma: no cover

    with pytest.raises(client.HomeAssistantError, match="Unexpected message"):
        run(go())


def test_command_error(fake_ha):
    async def go():
        async with client.connect(fake_ha.url, GOOD_TOKEN) as ha:
            await ha.call({"type": "nope"})

    with pytest.raises(client.CommandError, match="unknown_command: Unknown command.") as exc:
        run(go())
    assert exc.value.error == {"code": "unknown_command", "message": "Unknown command."}


@pytest.mark.parametrize(
    "error, message",
    [
        ({"message": "bad"}, "error: bad"),
        ("plain text", "plain text"),
        (None, "None"),
    ],
)
def test_command_error_messages(error, message):
    assert str(client.CommandError(error)) == message


@pytest.mark.parametrize(
    "url, base",
    [
        ("ws://homeassistant.local:8123/api/websocket", "http://homeassistant.local:8123"),
        ("wss://ha.example/api/websocket", "https://ha.example"),
        ("wss://proxy.example/ha/api/websocket", "https://proxy.example/ha"),
    ],
)
def test_rest_base_url(url, base):
    assert client.rest_base_url(url) == base


@pytest.mark.parametrize(
    "url", ["http://ha.example/api/websocket", "ws://ha.example/websocket", "ws://ha.example"]
)
def test_rest_base_url_needs_websocket_url(url):
    with pytest.raises(client.HomeAssistantError, match="can't work out the REST API address"):
        client.rest_base_url(url)


def rest(fake_ha, method, path, body=None, token=GOOD_TOKEN):
    async def go():
        async with client.connect(fake_ha.url, GOOD_TOKEN) as ha:
            ha._token = token
            return await ha.rest(method, path, body)

    return run(go())


def test_rest_get(fake_ha):
    fake_ha.automations["1"] = {"id": "1", "alias": "Heater off"}
    assert rest(fake_ha, "GET", "/api/config/automation/config/1") == {
        "id": "1",
        "alias": "Heater off",
    }


def test_rest_not_found(fake_ha):
    assert rest(fake_ha, "GET", "/api/config/automation/config/missing") is None


def test_rest_post(fake_ha):
    config = {"id": "1", "alias": "Heater on"}
    assert rest(fake_ha, "POST", "/api/config/automation/config/1", config) == {"result": "ok"}
    assert fake_ha.automations["1"] == config
    assert fake_ha.saves == [("automation", "1", config)]


def test_rest_error(fake_ha):
    fake_ha.fail_saves = {"1"}
    with pytest.raises(client.CommandError, match="http_400: Message malformed") as exc:
        rest(fake_ha, "POST", "/api/config/automation/config/1", {"id": "1"})
    assert exc.value.error["code"] == "http_400"


def test_rest_sends_token(fake_ha):
    with pytest.raises(client.CommandError, match="http_401: Unauthorized"):
        rest(fake_ha, "GET", "/api/config/automation/config/1", token="wrong")


def test_rest_error_without_json_message(monkeypatch):
    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 502, "Bad Gateway", email.message.Message(), io.BytesIO(b"<html>")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    request = urllib.request.Request("http://ha.example/api/config/automation/config/1")
    with pytest.raises(client.CommandError, match="http_502: Bad Gateway"):
        client._send(request)
