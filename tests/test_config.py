from pathlib import Path

import pytest

from pymusicbot.config import ConfigError, PlayerConfig, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


def env_for(tmp_path, toml: str | None = None, **extra) -> dict[str, str]:
    config = tmp_path / "config.toml"
    if toml is not None:
        config.write_text(toml, encoding="utf-8")
    return {
        "DISCORD_TOKEN": "token",
        "PYMUSICBOT_CONFIG": str(config),
        "PYMUSICBOT_DATA": str(tmp_path / "data"),
        **extra,
    }


def test_missing_file_uses_defaults(tmp_path):
    config = load_config(env_for(tmp_path))
    assert config.owner_id is None
    assert config.player == PlayerConfig()
    assert config.files.music_folders == (Path("music"),)  # in the working directory
    assert config.files.playlists_folder == tmp_path / "data" / "Playlists"


def test_paths_default_to_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # an empty directory, so no stray config.toml is read
    config = load_config({"DISCORD_TOKEN": "x"})
    assert config.data_dir == Path("data")
    assert config.files.music_folders == (Path("music"),)


def test_docker_sets_the_default_music_folder(tmp_path):
    config = load_config(env_for(tmp_path, PYMUSICBOT_MUSIC="/music"))
    assert config.files.music_folders == (Path("/music"),)
    configured = load_config(env_for(tmp_path, '[files]\nmusic_folders = ["/library"]', PYMUSICBOT_MUSIC="/music"))
    assert configured.files.music_folders == (tmp_path / "data" / "/library",)  # the config file wins


def test_example_file_is_valid_and_matches_defaults(tmp_path):
    defaults = load_config(env_for(tmp_path))
    example = load_config(env_for(tmp_path, EXAMPLE.read_text(encoding="utf-8")))
    assert example == defaults


def test_token_is_required(tmp_path):
    env = env_for(tmp_path)
    del env["DISCORD_TOKEN"]
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config(env)


def test_token_is_not_in_repr(tmp_path):
    assert "s3cret" not in repr(load_config(env_for(tmp_path, DISCORD_TOKEN="s3cret")))


def test_values_are_read(tmp_path):
    config = load_config(env_for(tmp_path, """
owner_id = 42
log_level = "DEBUG"
[presence]
status = "DND"
song_in_status = true
[player]
skip_ratio = 1
max_track_length = 600
[files]
music_folders = ["/music", "extra"]
playlists_folder = "/lists"
"""))
    assert config.owner_id == 42
    assert config.log_level == "debug"
    assert config.presence.status == "dnd"
    assert config.presence.song_in_status is True
    assert config.player.skip_ratio == 1.0
    assert config.player.max_track_length == 600
    data = tmp_path / "data"
    assert config.files.music_folders == (data / "/music", data / "extra")  # absolute stays absolute
    assert config.files.playlists_folder == data / "/lists"


def test_owner_id_from_env_wins(tmp_path):
    assert load_config(env_for(tmp_path, "owner_id = 1", OWNER_ID="2")).owner_id == 2


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("songinstatus = true", "Unknown setting songinstatus"),
        ("[player]\nskip_raito = 0.5", "Did you mean player.skip_ratio"),
        ("[player]\nskip_ratio = 1.5", "between 0 and 1"),
        ("[player]\nmax_track_length = -1", "can't be negative"),
        ("[player]\nstay_in_channel = 1", "must be true or false"),
        ("[presence]\nstatus = 'away'", "presence.status must be one of"),
        ("[files]\nmusic_folders = '/music'", "must be a list of strings"),
        ("player = 1", "must be a table"),
        ("log_level = 'loud'", "log_level must be one of"),
        ("owner_id = 0", "owner_id must be a Discord user ID"),
        ("this is not toml", "not valid TOML"),
    ],
)
def test_invalid_config(tmp_path, toml, message):
    with pytest.raises(ConfigError, match=message):
        load_config(env_for(tmp_path, toml))
