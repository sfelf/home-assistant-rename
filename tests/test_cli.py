import subprocess
import sys

import pytest

from ha_rename import __version__, cli
from tests.conftest import GOOD_TOKEN

PREFIX_ARGS = [
    "--old-id-prefix", "skyvane_skyvane_station_backyard_station_",
    "--new-id-prefix", "skyvane_station_",
    "--entity-name-prefix", "Backyard Station",
]


@pytest.fixture
def answer_prompt(monkeypatch):
    """Answer the hidden token prompt with the given value, recording each prompt."""
    prompts = []

    def set_answer(value):
        def fake_getpass(prompt=""):
            prompts.append(prompt)
            return value

        monkeypatch.setattr("getpass.getpass", fake_getpass)
        return prompts

    return set_answer


def store(memory_keyring, url, token=GOOD_TOKEN):
    memory_keyring.set_password("ha-rename", url, token)


class TestParser:
    def parse(self, *args):
        return cli.build_parser().parse_args([*PREFIX_ARGS, *args])

    def test_defaults(self):
        args = self.parse()
        assert args.ha_url == cli.DEFAULT_URL
        assert args.dry_run is False
        assert args.reset_token is False
        assert args.old_id_prefix == "skyvane_skyvane_station_backyard_station_"
        assert args.new_id_prefix == "skyvane_station_"
        assert args.entity_name_prefix == "Backyard Station"

    def test_options(self):
        args = self.parse("--ha-url", "wss://ha.example/api/websocket", "--dry-run", "--reset-token")
        assert args.ha_url == "wss://ha.example/api/websocket"
        assert args.dry_run is True
        assert args.reset_token is True

    def test_empty_new_prefix_allowed(self):
        args = cli.build_parser().parse_args(
            ["--old-id-prefix", "a_", "--new-id-prefix", "", "--entity-name-prefix", "A"])
        assert args.new_id_prefix == ""

    @pytest.mark.parametrize("missing", ["--old-id-prefix", "--new-id-prefix", "--entity-name-prefix"])
    def test_prefixes_are_required(self, missing, capsys):
        i = PREFIX_ARGS.index(missing)
        argv = PREFIX_ARGS[:i] + PREFIX_ARGS[i + 2:]
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(argv)
        assert exc.value.code == 2
        assert missing in capsys.readouterr().err

    @pytest.mark.parametrize("option, value", [
        ("--old-id-prefix", ""),
        ("--old-id-prefix", "Bad-Prefix"),
        ("--new-id-prefix", "bad prefix"),
    ])
    def test_invalid_prefixes(self, option, value, capsys):
        argv = [*PREFIX_ARGS, option, value]
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(argv)
        assert exc.value.code == 2
        assert option in capsys.readouterr().err

    def test_help_documents_everything(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for text in ["--old-id-prefix", "--new-id-prefix", "--entity-name-prefix", "--ha-url",
                     "--dry-run", "--reset-token", "--print-completion", "--version",
                     "Long-lived access tokens", "keyring", "examples:"]:
            assert text in out

    def test_version(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--version"])
        assert capsys.readouterr().out.strip() == f"ha-rename {__version__}"

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_print_completion_needs_no_other_options(self, shell, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--print-completion", shell])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "old-id-prefix" in out
        assert "dry-run" in out


class TestMain:
    def test_apply_with_stored_token(self, fake_ha, memory_keyring, capsys):
        store(memory_keyring, fake_ha.url)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 0

        assert fake_ha.updates == [
            {"type": "config/entity_registry/update",
             "entity_id": "sensor.skyvane_skyvane_station_backyard_station_temperature",
             "new_entity_id": "sensor.skyvane_station_temperature", "name": "Temperature"},
            {"type": "config/entity_registry/update",
             "entity_id": "switch.skyvane_skyvane_station_backyard_station_heater",
             "new_entity_id": "switch.skyvane_station_heater", "name": "Heater"},
            {"type": "config/entity_registry/update",
             "entity_id": "sensor.skyvane_skyvane_station_backyard_station_humidity",
             "new_entity_id": "sensor.skyvane_station_humidity"},
        ]
        out = capsys.readouterr().out
        assert out.startswith("Found 3 matching entities\n")
        assert "  name: 'Backyard Station Temperature' -> 'Temperature'" in out
        assert "rename the device instead" in out
        assert out.rstrip().endswith("Done.")

    def test_dry_run_changes_nothing(self, fake_ha, memory_keyring, capsys):
        store(memory_keyring, fake_ha.url)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--dry-run"]) == 0
        assert fake_ha.updates == []
        out = capsys.readouterr().out
        assert "sensor.skyvane_station_temperature" in out
        assert out.rstrip().endswith("Dry run only; nothing changed.")

    def test_skips_entities_with_nothing_to_change(self, fake_ha, memory_keyring):
        store(memory_keyring, fake_ha.url)
        argv = ["--old-id-prefix", "kitchen", "--new-id-prefix", "kitchen",
                "--entity-name-prefix", "Nope", "--ha-url", fake_ha.url]
        assert cli.main(argv) == 0
        assert fake_ha.updates == []

    def test_update_errors_are_reported(self, fake_ha, memory_keyring, capsys):
        store(memory_keyring, fake_ha.url)
        fake_ha.fail_updates = {"switch.skyvane_skyvane_station_backyard_station_heater"}
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert len(fake_ha.updates) == 3  # keeps going after an error
        out = capsys.readouterr().out
        assert "  ERROR: invalid_info: Entity is already registered" in out
        assert "Done with 1 error(s)." in out

    def test_prompts_and_saves_token_when_none_stored(self, fake_ha, memory_keyring, answer_prompt):
        prompts = answer_prompt(GOOD_TOKEN)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--dry-run"]) == 0
        assert len(prompts) == 1
        assert memory_keyring.passwords == {("ha-rename", fake_ha.url): GOOD_TOKEN}

    def test_does_not_prompt_when_token_stored(self, fake_ha, memory_keyring):
        store(memory_keyring, fake_ha.url)
        # The autouse no_prompt fixture fails the test if getpass is called.
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--dry-run"]) == 0

    def test_rejected_new_token_is_not_saved(self, fake_ha, memory_keyring, answer_prompt, capsys):
        answer_prompt("wrong")
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert memory_keyring.passwords == {}
        err = capsys.readouterr().err
        assert "rejected the access token" in err
        assert "--reset-token" not in err

    def test_rejected_stored_token_suggests_reset(self, fake_ha, memory_keyring, capsys):
        store(memory_keyring, fake_ha.url, "expired")
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert "--reset-token" in capsys.readouterr().err
        assert fake_ha.updates == []

    def test_reset_token_replaces_stored_token(self, fake_ha, memory_keyring, answer_prompt):
        store(memory_keyring, fake_ha.url, "expired")
        prompts = answer_prompt(GOOD_TOKEN)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--reset-token", "--dry-run"]) == 0
        assert len(prompts) == 1
        assert memory_keyring.passwords == {("ha-rename", fake_ha.url): GOOD_TOKEN}

    def test_empty_token_entered(self, memory_keyring, answer_prompt, capsys):
        answer_prompt("")
        assert cli.main(PREFIX_ARGS) == 1
        assert "No token entered" in capsys.readouterr().err

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
    def test_prompt_cancelled(self, monkeypatch, exc):
        def cancel(prompt=""):
            raise exc

        monkeypatch.setattr("getpass.getpass", cancel)
        assert cli.main(PREFIX_ARGS) == 130

    def test_keyring_read_failure(self, monkeypatch, capsys):
        from keyring.errors import KeyringError

        def boom(*a):
            raise KeyringError("no backend")

        monkeypatch.setattr("keyring.get_password", boom)
        assert cli.main(PREFIX_ARGS) == 1
        assert "Could not read from the system keyring: no backend" in capsys.readouterr().err

    def test_keyring_save_failure(self, fake_ha, monkeypatch, answer_prompt, capsys):
        from keyring.errors import KeyringError

        def boom(*a):
            raise KeyringError("read-only")

        monkeypatch.setattr("keyring.set_password", boom)
        answer_prompt(GOOD_TOKEN)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert "Could not save to the system keyring: read-only" in capsys.readouterr().err
        assert fake_ha.updates == []

    def test_protocol_error(self, fake_ha, memory_keyring, capsys):
        store(memory_keyring, fake_ha.url)
        fake_ha.first_message = {"type": "hello"}
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert "Unexpected message" in capsys.readouterr().err

    def test_connection_refused(self, memory_keyring, capsys):
        url = "ws://127.0.0.1:1/api/websocket"
        store(memory_keyring, url)
        assert cli.main([*PREFIX_ARGS, "--ha-url", url]) == 1
        assert f"could not connect to {url}" in capsys.readouterr().err

    def test_invalid_url(self, memory_keyring, capsys):
        url = "http://not-a-websocket-url"
        store(memory_keyring, url)
        assert cli.main([*PREFIX_ARGS, "--ha-url", url]) == 1
        assert f"could not connect to {url}" in capsys.readouterr().err

    def test_run_without_callback(self, fake_ha, capsys):
        import asyncio

        args = cli.build_parser().parse_args([*PREFIX_ARGS, "--dry-run"])
        assert asyncio.run(cli.run(fake_ha.url, GOOD_TOKEN, args)) == 0

    def test_interrupted_while_running(self, monkeypatch, memory_keyring):
        store(memory_keyring, cli.DEFAULT_URL)

        def interrupt(coro):
            coro.close()
            raise KeyboardInterrupt

        monkeypatch.setattr("asyncio.run", interrupt)
        assert cli.main(PREFIX_ARGS) == 130


def test_python_dash_m():
    result = subprocess.run([sys.executable, "-m", "ha_rename", "--version"],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == f"ha-rename {__version__}"
