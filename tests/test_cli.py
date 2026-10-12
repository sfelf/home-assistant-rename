import json
import subprocess
import sys
from pathlib import Path

import pytest

from ha_rename import __version__, cli, undo
from ha_rename.undo import UndoLog
from tests.conftest import GOOD_TOKEN, SAMPLE_ENTITIES

PREFIX_ARGS = [
    "--old-id-prefix",
    "skyvane_skyvane_station_backyard_station_",
    "--new-id-prefix",
    "skyvane_station_",
    "--entity-name-prefix",
    "Backyard Station",
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
        assert args.ha_url is None  # resolved to DEFAULT_URL (or the undo file's URL) in main
        assert args.undo is None
        assert args.undo_file is None
        assert args.dry_run is False
        assert args.reset_token is False
        assert args.old_id_prefix == "skyvane_skyvane_station_backyard_station_"
        assert args.new_id_prefix == "skyvane_station_"
        assert args.entity_name_prefix == "Backyard Station"

    def test_options(self):
        args = self.parse(
            "--ha-url", "wss://ha.example/api/websocket", "--dry-run", "--reset-token"
        )
        assert args.ha_url == "wss://ha.example/api/websocket"
        assert args.dry_run is True
        assert args.reset_token is True

    def test_empty_new_prefix_allowed(self):
        args = cli.build_parser().parse_args(
            ["--old-id-prefix", "a_", "--new-id-prefix", "", "--entity-name-prefix", "A"]
        )
        assert args.new_id_prefix == ""

    @pytest.mark.parametrize(
        "missing", ["--old-id-prefix", "--new-id-prefix", "--entity-name-prefix"]
    )
    def test_prefixes_are_required_without_undo(self, missing, capsys):
        i = PREFIX_ARGS.index(missing)
        argv = PREFIX_ARGS[:i] + PREFIX_ARGS[i + 2 :]
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
        assert f"the following arguments are required: {missing}" in capsys.readouterr().err

    def test_all_prefixes_listed_when_none_given(self, capsys):
        with pytest.raises(SystemExit):
            cli.main([])
        err = capsys.readouterr().err
        assert "required: --old-id-prefix, --new-id-prefix, --entity-name-prefix" in err

    @pytest.mark.parametrize(
        "option, value",
        [
            ("--old-id-prefix", ""),
            ("--old-id-prefix", "Bad-Prefix"),
            ("--new-id-prefix", "bad prefix"),
        ],
    )
    def test_invalid_prefixes(self, option, value, capsys):
        argv = [*PREFIX_ARGS, option, value]
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(argv)
        assert exc.value.code == 2
        assert option in capsys.readouterr().err

    @pytest.mark.parametrize("old, new", [("sky_old_", "sky_new"), ("sky_old", "sky_new_")])
    def test_mismatched_underscores_rejected_before_connecting(self, old, new, capsys):
        argv = ["--old-id-prefix", old, "--new-id-prefix", new, "--entity-name-prefix", "X"]
        # The autouse no_prompt fixture proves we exit before asking for a token.
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 2
        assert "ends with '_'" in capsys.readouterr().err

    def test_help_documents_everything(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for text in [
            "--old-id-prefix",
            "--new-id-prefix",
            "--entity-name-prefix",
            "--ha-url",
            "--dry-run",
            "--reset-token",
            "--print-completion",
            "--version",
            "Long-lived access tokens",
            "keyring",
            "examples:",
        ]:
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
            {
                "type": "config/entity_registry/update",
                "entity_id": "sensor.skyvane_skyvane_station_backyard_station_temperature",
                "new_entity_id": "sensor.skyvane_station_temperature",
                "name": "Temperature",
            },
            {
                "type": "config/entity_registry/update",
                "entity_id": "switch.skyvane_skyvane_station_backyard_station_heater",
                "new_entity_id": "switch.skyvane_station_heater",
                "name": "Heater",
            },
            {
                "type": "config/entity_registry/update",
                "entity_id": "sensor.skyvane_skyvane_station_backyard_station_humidity",
                "new_entity_id": "sensor.skyvane_station_humidity",
            },
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
        argv = [
            "--old-id-prefix",
            "kitchen",
            "--new-id-prefix",
            "kitchen",
            "--entity-name-prefix",
            "Nope",
            "--ha-url",
            fake_ha.url,
        ]
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
        assert f"connection to {url} failed" in capsys.readouterr().err

    def test_invalid_url(self, memory_keyring, capsys):
        url = "http://not-a-websocket-url"
        store(memory_keyring, url)
        assert cli.main([*PREFIX_ARGS, "--ha-url", url]) == 1
        assert f"connection to {url} failed" in capsys.readouterr().err

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


def undo_files(directory):
    return sorted(directory.glob("ha-rename-undo-*.jsonl"))


def stored(memory_keyring, fake_ha):
    store(memory_keyring, fake_ha.url)
    return fake_ha


class TestUndoFile:
    def test_apply_writes_undo_file(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        stored(memory_keyring, fake_ha)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 0
        [path] = undo_files(in_tmp_dir)
        header, *entries = [json.loads(line) for line in path.read_text().splitlines()]
        assert header["ha_url"] == fake_ha.url
        assert [entry["change"] for entry in entries] == [
            {
                "entity_id": "sensor.skyvane_skyvane_station_backyard_station_temperature",
                "new_entity_id": "sensor.skyvane_station_temperature",
                "name": None,
                "new_name": "Temperature",
                "registry_id": "reg-sensor.skyvane_skyvane_station_backyard_station_temperature",
            },
            {
                "entity_id": "switch.skyvane_skyvane_station_backyard_station_heater",
                "new_entity_id": "switch.skyvane_station_heater",
                "name": "Backyard Station Heater",
                "new_name": "Heater",
                "registry_id": "reg-switch.skyvane_skyvane_station_backyard_station_heater",
            },
            {
                "entity_id": "sensor.skyvane_skyvane_station_backyard_station_humidity",
                "new_entity_id": "sensor.skyvane_station_humidity",
                "name": None,
                "new_name": None,
                "registry_id": "reg-sensor.skyvane_skyvane_station_backyard_station_humidity",
            },
        ]
        out = capsys.readouterr().out
        assert f"Undo file: {path.name}" in out
        assert f"To reverse these changes, run: ha-rename --undo {path.name}" in out

    def test_undo_file_option(self, fake_ha, memory_keyring, in_tmp_dir):
        stored(memory_keyring, fake_ha)
        target = in_tmp_dir / "my undo.jsonl"
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--undo-file", str(target)]) == 0
        assert len(undo.load(target).changes) == 3
        assert undo_files(in_tmp_dir) == []

    def test_dry_run_writes_no_undo_file(self, fake_ha, memory_keyring, in_tmp_dir):
        stored(memory_keyring, fake_ha)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url, "--dry-run"]) == 0
        assert list(in_tmp_dir.iterdir()) == []

    def test_no_changes_writes_no_undo_file(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        stored(memory_keyring, fake_ha)
        argv = ["--old-id-prefix", "nothing_", "--new-id-prefix", "else_"]
        assert cli.main([*argv, "--entity-name-prefix", "X", "--ha-url", fake_ha.url]) == 0
        assert list(in_tmp_dir.iterdir()) == []
        assert "Undo file" not in capsys.readouterr().out

    def test_only_successful_updates_are_recorded(self, fake_ha, memory_keyring, in_tmp_dir):
        stored(memory_keyring, fake_ha)
        fake_ha.fail_updates = {"switch.skyvane_skyvane_station_backyard_station_heater"}
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        [path] = undo_files(in_tmp_dir)
        recorded = [c.entity_id for c in undo.load(path).changes]
        assert "switch.skyvane_skyvane_station_backyard_station_heater" not in recorded
        assert len(recorded) == 2

    def test_all_updates_failing_leaves_no_undo_file(self, fake_ha, memory_keyring, in_tmp_dir):
        stored(memory_keyring, fake_ha)
        fake_ha.fail_updates = {e["entity_id"] for e in fake_ha.entities}
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert list(in_tmp_dir.iterdir()) == []

    def test_unwritable_undo_file_changes_nothing(
        self, fake_ha, memory_keyring, in_tmp_dir, capsys
    ):
        stored(memory_keyring, fake_ha)
        existing = in_tmp_dir / "taken.jsonl"
        existing.write_text("{}")
        argv = [*PREFIX_ARGS, "--ha-url", fake_ha.url, "--undo-file", str(existing)]
        assert cli.main(argv) == 1
        assert fake_ha.updates == []
        assert "could not create undo file" in capsys.readouterr().err

    def test_undo_file_update_failure_stops_run(self, fake_ha, memory_keyring, monkeypatch, capsys):
        stored(memory_keyring, fake_ha)
        real_open = Path.open

        def open_(self, mode="r", *args, **kwargs):
            if mode == "a":
                raise OSError(28, "No space left on device")
            return real_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert fake_ha.updates == []  # each change is recorded before it's sent
        err = capsys.readouterr().err
        assert "Stopped before changing sensor.skyvane_skyvane_station_backyard_station" in err

    def test_change_with_lost_reply_is_still_recorded(
        self, fake_ha, memory_keyring, in_tmp_dir, capsys
    ):
        stored(memory_keyring, fake_ha)
        heater = "switch.skyvane_skyvane_station_backyard_station_heater"
        fake_ha.drop_after_update = {heater}
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 1
        assert "failed" in capsys.readouterr().err
        [path] = undo_files(in_tmp_dir)
        recorded = [c.entity_id for c in undo.load(path).changes]
        assert recorded[-1] == heater  # applied, but the reply never arrived
        fake_ha.drop_after_update = set()
        assert cli.main(["--undo", str(path)]) == 0
        assert fake_ha.find(heater)["name"] == "Backyard Station Heater"
        assert fake_ha.find("sensor.skyvane_skyvane_station_backyard_station_temperature")


def test_default_undo_path_is_numbered_when_taken(in_tmp_dir, monkeypatch):
    first = cli.default_undo_path()
    assert first.name.startswith("ha-rename-undo-")
    first.write_text("{}")

    class Frozen(cli.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.strptime(first.stem.removeprefix("ha-rename-undo-"), "%Y%m%d-%H%M%S")

    monkeypatch.setattr(cli, "datetime", Frozen)
    second = cli.default_undo_path()
    assert second.name == f"{first.stem}-2.jsonl"
    second.write_text("{}")
    assert cli.default_undo_path().name == f"{first.stem}-3.jsonl"


class TestUndo:
    def apply(self, fake_ha, memory_keyring, in_tmp_dir):
        stored(memory_keyring, fake_ha)
        assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 0
        [path] = undo_files(in_tmp_dir)
        return path

    def test_undo_restores_everything(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        capsys.readouterr()
        assert cli.main(["--undo", str(path)]) == 0  # URL comes from the undo file
        assert fake_ha.entities == SAMPLE_ENTITIES
        out = capsys.readouterr().out
        assert out.startswith(f"Undoing 3 change(s) from {path}\n")
        assert "  name: 'Temperature' -> (integration's name)" in out
        assert "  name: 'Heater' -> 'Backyard Station Heater'" in out

    def test_undo_dry_run(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        renamed = [dict(e) for e in fake_ha.entities]
        updates = len(fake_ha.updates)
        assert cli.main(["--undo", str(path), "--dry-run"]) == 0
        assert fake_ha.entities == renamed
        assert len(fake_ha.updates) == updates
        assert undo_files(in_tmp_dir) == [path]
        assert "Dry run only; nothing changed." in capsys.readouterr().out

    def test_undo_writes_its_own_undo_file_for_redo(self, fake_ha, memory_keyring, in_tmp_dir):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        renamed = [dict(e) for e in fake_ha.entities]
        redo = in_tmp_dir / "redo.jsonl"
        assert cli.main(["--undo", str(path), "--undo-file", str(redo)]) == 0
        assert fake_ha.entities == SAMPLE_ENTITIES
        assert cli.main(["--undo", str(redo)]) == 0
        assert fake_ha.entities == renamed

    def test_undo_ignores_replacement_with_same_entity_id(
        self, fake_ha, memory_keyring, in_tmp_dir, capsys
    ):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        heater = fake_ha.find("switch.skyvane_station_heater")
        heater["id"] = "reg-a-different-entity"  # removed, then a new entity took its ID
        assert cli.main(["--undo", str(path)]) == 1
        assert heater["entity_id"] == "switch.skyvane_station_heater"
        assert "SKIPPED: not found in Home Assistant (removed since?)" in capsys.readouterr().out

    def test_undo_follows_entities_renamed_since(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        heater = fake_ha.find("switch.skyvane_station_heater")
        heater["entity_id"] = "switch.garage_heater"
        assert cli.main(["--undo", str(path)]) == 0
        assert heater["entity_id"] == "switch.garage_heater"
        assert heater["name"] == "Backyard Station Heater"
        assert "entity ID: changed since to switch.garage_heater" in capsys.readouterr().out

    def test_undo_skips_missing_entities(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        fake_ha.entities = [
            e for e in fake_ha.entities if e["entity_id"] != "switch.skyvane_station_heater"
        ]
        assert cli.main(["--undo", str(path)]) == 1
        out = capsys.readouterr().out
        assert "switch.skyvane_station_heater\n  SKIPPED: not found" in out
        assert "Done with 1 error(s)." in out
        assert fake_ha.find("sensor.skyvane_skyvane_station_backyard_station_temperature")

    def test_undo_leaves_names_changed_since(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        fake_ha.find("sensor.skyvane_station_temperature")["name"] = "Outdoor temperature"
        assert cli.main(["--undo", str(path)]) == 0
        restored = fake_ha.find("sensor.skyvane_skyvane_station_backyard_station_temperature")
        assert restored["name"] == "Outdoor temperature"
        assert "changed since to 'Outdoor temperature'; left as is" in capsys.readouterr().out

    def test_ha_url_overrides_undo_file(self, fake_ha, memory_keyring, in_tmp_dir, capsys):
        path = self.apply(fake_ha, memory_keyring, in_tmp_dir)
        other = fake_ha.url.replace("127.0.0.1", "localhost")
        store(memory_keyring, other)
        assert cli.main(["--undo", str(path), "--ha-url", other, "--dry-run"]) == 0
        assert f"was recorded against {fake_ha.url}; using {other}" in capsys.readouterr().err

    @pytest.mark.parametrize("option", ["--old-id-prefix", "--new-id-prefix"])
    def test_undo_cannot_be_combined_with_rename_options(self, option, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--undo", "x.jsonl", option, "abc_"])
        assert exc.value.code == 2
        assert f"--undo can't be combined with {option}" in capsys.readouterr().err

    def test_bad_undo_file_fails_before_prompting(self, in_tmp_dir, capsys):
        (in_tmp_dir / "bad.jsonl").write_text("nope")
        # The autouse no_prompt fixture fails the test if a token is asked for.
        assert cli.main(["--undo", "bad.jsonl"]) == 1
        assert "bad.jsonl is not a valid undo file" in capsys.readouterr().err

    def test_empty_undo_file(self, in_tmp_dir, capsys):
        UndoLog.create(in_tmp_dir / "empty.jsonl", "ws://ha.test/api/websocket")
        assert cli.main(["--undo", "empty.jsonl"]) == 0
        assert "Nothing to undo" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value, windows, expected",
    [
        ("undo.jsonl", False, "undo.jsonl"),
        ("/tmp/undo-1.jsonl", False, "/tmp/undo-1.jsonl"),
        ("my undo.jsonl", False, "'my undo.jsonl'"),
        ("foo&whoami.jsonl", False, "'foo&whoami.jsonl'"),
        ("it's.jsonl", False, "'it'\"'\"'s.jsonl'"),
        ("C:\\Users\\me\\undo.jsonl", True, "C:\\Users\\me\\undo.jsonl"),
        ("C:\\My Files\\undo.jsonl", True, '"C:\\My Files\\undo.jsonl"'),
        ("foo&whoami.jsonl", True, '"foo&whoami.jsonl"'),
        ("a%PATH%.jsonl", True, None),
        ("a!x!.jsonl", True, None),
        ("a$env:x.jsonl", True, None),
        ("a`b.jsonl", True, None),
    ],
)
def test_shell_quote(value, windows, expected):
    assert cli.shell_quote(value, windows=windows) == expected


def test_unquotable_undo_path_is_not_shown_as_a_command(
    fake_ha, memory_keyring, monkeypatch, capsys
):
    stored(memory_keyring, fake_ha)
    monkeypatch.setattr(cli, "shell_quote", lambda value: None)
    assert cli.main([*PREFIX_ARGS, "--ha-url", fake_ha.url]) == 0
    out = capsys.readouterr().out
    assert "To reverse these changes, pass that file to: ha-rename --undo\n" in out
    assert "run: ha-rename --undo" not in out


def test_python_dash_m():
    result = subprocess.run(
        [sys.executable, "-m", "ha_rename", "--version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == f"ha-rename {__version__}"
