import json

import pytest

from pymusicbot.settings import (
    LEGACY_SETTINGS_FILE,
    SETTINGS_FILE,
    GuildSettings,
    QueueType,
    RepeatMode,
    SettingsError,
    SettingsStore,
    convert_legacy,
)

# Same shape as a real JMusicBot serversettings.json.
LEGACY = {
    "111": {
        "voice_channel_id": "222",
        "text_channel_id": "333",
        "default_playlist": "GirlsBandCry",
    },
    "444": {
        "text_channel_id": "0",
        "voice_channel_id": "not a number",
        "dj_role_id": "555",
        "volume": 35,
        "repeat_mode": "SINGLE",
        "prefix": "!",
        "skip_ratio": 0.75,
        "queue_type": "LINEAR",
    },
    "666": {"repeat": True, "skip_ratio": -1},
}


def write_legacy(data_dir, data=LEGACY):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LEGACY_SETTINGS_FILE
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return path


def test_convert_legacy():
    guilds = convert_legacy(LEGACY)
    assert guilds[111] == GuildSettings(voice_channel_id=222, text_channel_id=333, default_playlist="GirlsBandCry")
    assert guilds[444] == GuildSettings(
        dj_role_id=555, volume=35, repeat_mode=RepeatMode.SINGLE, skip_ratio=0.75, queue_type=QueueType.LINEAR
    )
    assert guilds[666] == GuildSettings(repeat_mode=RepeatMode.ALL)  # pre-0.3.4 boolean repeat


def test_first_start_converts_and_leaves_legacy_untouched(tmp_path):
    legacy = write_legacy(tmp_path)
    before = legacy.read_bytes()

    store = SettingsStore.load(tmp_path)

    assert store.get(111).default_playlist == "GirlsBandCry"
    assert (tmp_path / SETTINGS_FILE).exists()
    assert legacy.read_bytes() == before


def test_legacy_is_ignored_once_new_file_exists(tmp_path):
    legacy = write_legacy(tmp_path)
    SettingsStore.load(tmp_path).update(111, volume=50)
    legacy.write_text(json.dumps({"111": {"volume": 5}}), encoding="utf-8")

    assert SettingsStore.load(tmp_path).get(111).volume == 50


def test_changes_persist(tmp_path):
    store = SettingsStore.load(tmp_path)
    assert not (tmp_path / SETTINGS_FILE).exists()  # nothing written until something changes

    store.update(777, dj_role_id=888, queue_type=QueueType.LINEAR, skip_ratio=0.5)
    store.update(777, volume=80)

    reloaded = SettingsStore.load(tmp_path).get(777)
    assert reloaded == GuildSettings(dj_role_id=888, volume=80, queue_type=QueueType.LINEAR, skip_ratio=0.5)


def test_ids_are_stored_as_strings(tmp_path):
    SettingsStore.load(tmp_path).update(123456789012345678, text_channel_id=987654321098765432)
    data = json.loads((tmp_path / SETTINGS_FILE).read_text(encoding="utf-8"))
    assert data["guilds"]["123456789012345678"]["text_channel_id"] == "987654321098765432"


def test_unknown_guild_gets_defaults(tmp_path):
    assert SettingsStore.load(tmp_path).get(1) == GuildSettings()


def test_no_temp_files_left_behind(tmp_path):
    SettingsStore.load(tmp_path).update(1, volume=10)
    assert sorted(p.name for p in tmp_path.iterdir()) == [SETTINGS_FILE]


@pytest.mark.parametrize("content", ["{not json", '{"version": 99, "guilds": {}}', '{"version": 1, "guilds": {"1": {"repeat_mode": "loud"}}}'])
def test_bad_settings_file_is_not_overwritten(tmp_path, content):
    path = tmp_path / SETTINGS_FILE
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError):
        SettingsStore.load(tmp_path)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("data", [[], {"1": "nope"}, {"1": {"repeat_mode": "SOMETIMES"}}, {"abc": {}}])
def test_bad_legacy_file_writes_nothing(tmp_path, data):
    write_legacy(tmp_path, data)
    with pytest.raises(SettingsError):
        SettingsStore.load(tmp_path)
    assert not (tmp_path / SETTINGS_FILE).exists()
