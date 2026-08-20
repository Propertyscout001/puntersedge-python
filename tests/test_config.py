"""Config resolution, permissions, and the boundary tripwire.

`env` and `config_file` are threaded in as parameters everywhere, so every test here is
hermetic. That is not incidental: an earlier shape of this loader read `os.environ`
inline, and its precedence tests silently resolved against the developer's own
`~/.config/puntersedge/config`.
"""
from __future__ import annotations

import os
import stat

import pytest

from puntersedge import ApiKeyError, ConfigError, resolve_api_key
from puntersedge.config import ConfigChain, default_config_path, load_config_file

KEY = "pe_live_TESTKEY"


def write_config(tmp_path, text, mode=0o600, name="config"):
    p = tmp_path / name
    p.write_text(text)
    os.chmod(p, mode)
    return p


# ── precedence ───────────────────────────────────────────────────────────────────────

def test_explicit_argument_wins(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = from_file\n")
    got = resolve_api_key("explicit", env={"PUNTERSEDGE_API_KEY": "from_env"}, config_file=p)
    assert got == "explicit"


def test_env_beats_file(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = from_file\n")
    assert resolve_api_key(env={"PUNTERSEDGE_API_KEY": "from_env"}, config_file=p) == "from_env"


def test_file_used_when_env_absent(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY)
    assert resolve_api_key(env={}, config_file=p) == KEY


def test_percent_in_key_survives(tmp_path):
    """RawConfigParser, not ConfigParser: a bare % is interpolation syntax to the latter."""
    p = write_config(tmp_path, "[puntersedge]\napi_key = pe_live_a%%b0c\n".replace("%%", "%"))
    assert resolve_api_key(env={}, config_file=p) == "pe_live_a%b0c"


def test_precedence_is_per_value_not_per_source(tmp_path):
    """The key from the environment must not discard the file's base_url.

    A per-source chain would point a CI run at production while the file said staging.
    """
    p = write_config(
        tmp_path, "[puntersedge]\napi_key = from_file\nbase_url = https://staging.example/v1\n"
    )
    chain = ConfigChain(env={"PUNTERSEDGE_API_KEY": "from_env"}, config_file=p)
    assert chain.get("api_key", env_names=("PUNTERSEDGE_API_KEY",)).value == "from_env"
    assert chain.get("base_url", env_names=("PUNTERSEDGE_BASE_URL",)).value == \
        "https://staging.example/v1"


# ── blank means unset ────────────────────────────────────────────────────────────────

def test_blank_env_is_unset_not_a_key(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY)
    assert resolve_api_key(env={"PUNTERSEDGE_API_KEY": ""}, config_file=p) == KEY


def test_whitespace_env_is_unset(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY)
    assert resolve_api_key(env={"PUNTERSEDGE_API_KEY": "   "}, config_file=p) == KEY


def test_blank_file_value_is_unset(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key =\n")
    with pytest.raises(ApiKeyError):
        resolve_api_key(env={}, config_file=p)


def test_explicit_empty_string_is_an_error_not_a_lookup(tmp_path):
    """Substituting a different key would transact under credentials never chosen."""
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY)
    with pytest.raises(ApiKeyError, match="empty string"):
        resolve_api_key("", env={}, config_file=p)


# ── missing / unreadable / malformed ─────────────────────────────────────────────────

def test_missing_explicit_path_is_hard_error(tmp_path):
    """The typo case. Falling through would silently resolve a DIFFERENT key."""
    with pytest.raises(ConfigError, match="no such file"):
        resolve_api_key(env={}, config_file=tmp_path / "nope")


def test_missing_default_path_is_not_an_error(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "empty")}
    with pytest.raises(ApiKeyError):  # ApiKeyError, not ConfigError
        resolve_api_key(env=env)


def test_directory_instead_of_file(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(ConfigError, match="directory"):
        load_config_file(d, required=True)


def test_malformed_ini_never_echoes_the_offending_text(tmp_path):
    """A parse error that quotes the bad line publishes the key to stderr and CI logs."""
    p = write_config(tmp_path, "this is not ini\napi_key = %s\n" % KEY)
    with pytest.raises(ConfigError) as ei:
        load_config_file(p, required=True)
    assert KEY not in str(ei.value)
    assert str(p) in str(ei.value)


def test_unreadable_is_not_treated_as_absent(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY, mode=0o000)
    if os.name != "nt" and os.geteuid() == 0:
        pytest.skip("root can read anything")
    with pytest.raises(ConfigError):
        load_config_file(p, required=False)


# ── permissions ──────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are meaningless on Windows")
def test_world_writable_is_refused(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY, mode=0o666)
    with pytest.raises(ConfigError, match="writable by other users"):
        load_config_file(p, required=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are meaningless on Windows")
def test_world_readable_warns_but_works(tmp_path):
    """0644 is what nano produces under a default umask — refusing would hit every
    first-time user, and the override they'd set would be permanent."""
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY, mode=0o644)
    with pytest.warns(UserWarning, match="readable by other users"):
        assert resolve_api_key(env={}, config_file=p) == KEY


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are meaningless on Windows")
def test_mode_777_warns_rather_than_refusing(tmp_path):
    """The WSL /mnt, Docker bind-mount and exFAT signature: the mode carries no info."""
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY, mode=0o777)
    with pytest.warns(UserWarning, match="0777"):
        assert resolve_api_key(env={}, config_file=p) == KEY


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are meaningless on Windows")
def test_0600_is_silent(tmp_path, recwarn):
    p = write_config(tmp_path, "[puntersedge]\napi_key = %s\n" % KEY, mode=0o600)
    assert resolve_api_key(env={}, config_file=p) == KEY
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


# ── the boundary, as code ────────────────────────────────────────────────────────────

def test_bookmaker_section_is_a_startup_error(tmp_path):
    p = write_config(tmp_path, "[sportsbet]\nusername = bob\npassword = hunter2\n")
    with pytest.raises(ConfigError) as ei:
        load_config_file(p, required=True)
    assert "never stores bookmaker logins" in str(ei.value)


def test_login_shaped_option_is_refused(tmp_path):
    p = write_config(tmp_path, "[puntersedge]\napi_key = k\npassword = hunter2\n")
    with pytest.raises(ConfigError, match="never stores bookmaker logins"):
        load_config_file(p, required=True)


def test_unknown_section_is_refused(tmp_path):
    p = write_config(tmp_path, "[whatever]\nx = 1\n")
    with pytest.raises(ConfigError, match="unknown section"):
        load_config_file(p, required=True)


def test_the_two_allowed_sections_are_fine(tmp_path):
    p = write_config(
        tmp_path, "[puntersedge]\napi_key = k\n\n[arb]\nmin_edge_pct = 0.5\n"
    )
    data = load_config_file(p, required=True)
    assert data["puntersedge"]["api_key"] == "k"
    assert data["arb"]["min_edge_pct"] == "0.5"


def test_no_symbol_in_the_package_is_named_credential():
    """`add my Sportsbet login to resolve_api_key` reads as obviously wrong.
    `add my Sportsbet login to credentials.py` reads as the intended use."""
    import pathlib

    import puntersedge

    root = pathlib.Path(puntersedge.__file__).parent
    for py in root.rglob("*.py"):
        text = py.read_text()
        for line in text.splitlines():
            if line.lstrip().startswith(("def ", "class ")):
                assert "credential" not in line.lower(), "%s: %s" % (py.name, line)


# ── the trace ────────────────────────────────────────────────────────────────────────

def test_terminal_error_names_every_source_and_leaks_nothing(tmp_path):
    p = write_config(tmp_path, "[arb]\nmin_edge_pct = 1\n")
    with pytest.raises(ApiKeyError) as ei:
        resolve_api_key(env={"PUNTERSEDGE_API_KEY": ""}, config_file=p)
    msg = str(ei.value)
    assert "set but EMPTY" in msg          # distinguishes empty from unset
    assert "no [puntersedge] section" in msg
    assert "found: arb" in msg
    assert "api-platform#signup" in msg


def test_home_unset_yields_no_path_not_a_tilde_directory():
    """expanduser('~/x') with HOME unset returns '~/x' verbatim, and a writer would then
    create a literal '~' directory inside the user's repo."""
    assert default_config_path(env={}) is None


def test_xdg_config_home_respected(tmp_path):
    got = default_config_path(env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert got == str(tmp_path / "puntersedge" / "config")



# ── one file, two independent readers ────────────────────────────────────────────────

def test_gateconfig_load_reads_arb_section(tmp_path):
    from puntersedge.arb import GateConfig, UnknownAge

    p = write_config(
        tmp_path,
        "[puntersedge]\napi_key = %s\n\n[arb]\nbettable_books = sportsbet, TAB\n"
        "min_edge_pct = 0.5\nmax_quote_age_s = 90\nunknown_age = allow\n" % KEY,
    )
    cfg = GateConfig.load(env={}, config_file=p)
    assert cfg.bettable_books == {"sportsbet", "tab"}
    assert cfg.min_edge_pct == 0.5
    assert cfg.max_quote_age_s == 90.0
    assert cfg.unknown_age is UnknownAge.ALLOW


def test_gateconfig_env_beats_file_and_pe_alias_still_works(tmp_path):
    from puntersedge.arb import GateConfig

    p = write_config(tmp_path, "[arb]\nmin_edge_pct = 0.5\n")
    assert GateConfig.load(
        env={"PUNTERSEDGE_ARB_MIN_EDGE_PCT": "2.5"}, config_file=p
    ).min_edge_pct == 2.5
    # PE_ARB_* shipped in 0.2.0. An unread gate variable does not raise — it reverts the
    # gate to its default and quietly loosens what the user is willing to bet on.
    assert GateConfig.load(env={"PE_ARB_MIN_EDGE_PCT": "3.5"}, config_file=p).min_edge_pct == 3.5


def test_bad_arb_value_names_the_setting_and_its_source(tmp_path):
    from puntersedge.arb import GateConfig

    p = write_config(tmp_path, "[arb]\nmin_edge_pct = abc\n")
    with pytest.raises(ValueError) as ei:
        GateConfig.load(env={}, config_file=p)
    assert "min_edge_pct" in str(ei.value)
    assert str(p) in str(ei.value)      # which of the two possible sources


def test_client_never_reads_arb_and_gateconfig_never_reads_the_key(tmp_path):
    from puntersedge import PuntersEdge
    from puntersedge.arb import GateConfig

    p = write_config(
        tmp_path, "[puntersedge]\napi_key = %s\n\n[arb]\nmin_edge_pct = 0.5\n" % KEY
    )
    pe = PuntersEdge(env={}, config_file=p)
    assert not hasattr(pe, "min_edge_pct")
    cfg = GateConfig.load(env={}, config_file=p)
    assert KEY not in str(vars(cfg))
