import asyncio

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
