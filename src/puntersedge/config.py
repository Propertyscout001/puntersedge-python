"""Where the API key and settings come from, and in what order.

The only module in this package that touches disk or `configparser`.

WHAT THIS HOLDS
---------------
One PuntersEdge API key, plus non-secret settings. That is the whole list, and it is
enforced rather than documented — see `_BOOKS` / `_LOGIN_FIELDS` below. This package never
holds bookmaker credentials, never places bets, and never operates a betting account. It
reads odds and computes sizing; you place every bet yourself in your own session.

The module is `config.py` and the public function is `resolve_api_key()`. Neither is named
"credentials", and that is deliberate: "add my Sportsbet login to `resolve_api_key`" reads
as obviously wrong, where "add my Sportsbet login to `credentials.py`" reads as the intended
use. A generic plural noun has no ceiling.

FILE FORMAT
-----------
INI, via `RawConfigParser`. Not TOML: `tomllib` is 3.11+ and this package supports 3.8, so
TOML means a conditional `tomli` dependency plus an import shim whose 3.8/3.9/3.10 branch no
CI job here would ever execute, failing as an `ImportError` at `import puntersedge` for
exactly the users it exists to serve. Not JSON: a `# comment` is a syntax error in a file
whose purpose is to be hand-annotated. Not python-dotenv: a new dependency whose model is
mutating `os.environ` process-wide, which leaks the key into every subprocess.

RawConfigParser rather than ConfigParser because a single `%` in an API key raises
`InterpolationSyntaxError`, and the error text talks about string interpolation — giving the
user no hint that their key is fine.

    [puntersedge]
    api_key  = 3f8b1c04-5e7a-4d21-9b6e-0a2c8d5f1e93
    base_url = https://api.puntersedge.online/v1
    timeout  = 30
    retries  = 3

    [arb]
    bettable_books  = sportsbet, tab, neds
    min_edge_pct    = 0.5
    max_edge_pct    = 8
    max_quote_age_s = 120
    unknown_age     = reject

    [alerts]
    webhook_url  = https://discord.com/api/webhooks/...
    min_edge_pct = 1.0
    cooldown_s   = 3600
    max_per_hour = 20

One file, three independent readers: `PuntersEdge` reads only `[puntersedge]`, `GateConfig`
only `[arb]`, and the alerter only `[alerts]`. None of them can see another's secrets.
"""
from __future__ import annotations

import configparser
import os
import stat
import warnings
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Union

from .exceptions import ApiKeyError, ConfigError

SECTION = "puntersedge"
ARB_SECTION = "arb"
# `[alerts]` holds one more credential — an incoming-webhook URL, which is a bearer token in
# URL form. Adding it widens the allowlist by exactly one named section and does NOT weaken
# the boundary: the book/login tripwire below still applies to every section and option, so
# `[sportsbet]` and `password =` remain startup errors wherever they appear. The distinction
# is not "is it secret" but "is it a bookmaker account" — a webhook to your own chat channel
# is not one, and nothing in this package can bet through it.
ALERT_SECTION = "alerts"
ALLOWED_SECTIONS = frozenset({SECTION, ARB_SECTION, ALERT_SECTION})
ENV_PREFIX = "PUNTERSEDGE_"
CONFIG_FILE_ENV = ENV_PREFIX + "CONFIG_FILE"

SIGNUP_URL = "https://puntersedge.online/api-platform#signup"

PathLike = Union[str, "os.PathLike[str]"]


# ── the boundary, as code ────────────────────────────────────────────────────────────
_BOUNDARY_MSG = (
    "puntersedge stores only its own API key. It never stores bookmaker logins and never "
    "places bets — place your own bets yourself, in your own logged-in session."
)

# A config file naming a bookmaker, or carrying a login-shaped option, is refused outright.
# This turns a product ruling into a testable failure with a self-explaining message,
# instead of a line of documentation that a user pastes past.
_BOOKS = frozenset({
    "sportsbet", "tab", "tabtouch", "betfair", "betfair_ex_au", "neds", "ladbrokes",
    "ladbrokes_au", "unibet", "pointsbet", "pointsbetau", "betr", "betr_au", "betright",
    "palmerbet", "bluebet", "dabble", "picklebet", "topsport", "playup", "boombet",
})
_LOGIN_FIELDS = frozenset({
    "username", "user", "password", "pass", "passwd", "pin", "login", "account",
    "account_number", "session", "cookie", "otp", "totp", "credentials", "credential",
})


def _check_boundary(path: object, name: str) -> None:
    """Refuse a section or option name that implies holding a bookmaker account."""
    if name.strip().lower() in _BOOKS or name.strip().lower() in _LOGIN_FIELDS:
        raise ConfigError("%s: %r — %s" % (path, name, _BOUNDARY_MSG))


# ── blank means unset, everywhere ────────────────────────────────────────────────────
def _nonblank(value: Optional[str]) -> Optional[str]:
    """The single implementation of "blank means unset".

    Applied identically to environment values, file values, and explicit arguments, so the
    three sources cannot disagree about what an empty string means.

    The failure this prevents is specific and common: `if api_key is None: api_key =
    os.environ.get(...)` treats `PUNTERSEDGE_API_KEY=""` as a real key, skips the friendly
    local error, and hands the user a bare server 401 with no hint that their variable is
    empty. `GateConfig.from_env` in this package already got this right; this is that rule,
    lifted out so there is one copy.
    """
    if value is None:
        return None
    text = value.strip()
    return text or None


# ── where the file lives ─────────────────────────────────────────────────────────────
def default_config_path(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The default config path, or None when the home directory cannot be determined.

    Deliberately NOT `os.path.expanduser("~/.config/...")`. With `HOME` unset — systemd
    units, some containers, some CI runners — `expanduser` returns the string unchanged,
    so a caller that later writes to it creates a directory literally named `~` inside
    whatever the current working directory happens to be. Returning None lets the caller
    skip the tier and say so, rather than inventing a path.
    """
    env = os.environ if env is None else env
    if os.name == "nt":
        appdata = _nonblank(env.get("APPDATA"))
        return os.path.join(appdata, "puntersedge", "config") if appdata else None
    xdg = _nonblank(env.get("XDG_CONFIG_HOME"))
    if xdg:
        return os.path.join(xdg, "puntersedge", "config")
    home = _nonblank(env.get("HOME"))
    return os.path.join(home, ".config", "puntersedge", "config") if home else None


# NOTE: there is deliberately no current-directory tier and no walk-up search.
# A novel filename in the working directory is in nobody's gitignore template, so the first
# `git add .` commits a live key; it makes the resolved key a function of `cd`, so the same
# script picks up different credentials under cron than interactively, silently either way.
# Project-local config is already expressible as PUNTERSEDGE_CONFIG_FILE=./x — explicit,
# greppable, and impossible to acquire by accident.


# ── permissions ──────────────────────────────────────────────────────────────────────
def _check_mode(path: object, st: os.stat_result) -> None:
    """Refuse a file that is not ours or is writable by others; warn if it is readable.

    On Windows this is skipped entirely and NO reassuring signal is emitted either:
    `st_mode` there is synthesised from the read-only attribute and never reflects the ACL,
    so a check would manufacture a green light over a file nobody verified. `%APPDATA%` is
    per-user ACL'd, which is the real protection on that platform.
    """
    if os.name == "nt":
        return

    if not stat.S_ISREG(st.st_mode):
        raise ConfigError("%s: not a regular file." % (path,))

    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise ConfigError(
            "%s: owned by uid %d, not you (uid %d). Refusing to read an API key from a "
            "file you do not own." % (path, st.st_uid, os.geteuid())
        )

    mode = stat.S_IMODE(st.st_mode)

    # Exactly 0777 is the WSL /mnt, Docker-bind-mount-from-macOS, exFAT and many-NFS
    # signature, where the mode carries no information at all. Hard-refusing there breaks
    # working setups and teaches people to disable the check permanently.
    if mode == 0o777:
        warnings.warn(
            "%s is mode 0777, which usually means a filesystem that does not carry POSIX "
            "permissions (WSL /mnt, a Docker bind mount, exFAT). Its permissions could not "
            "be verified." % (path,),
            UserWarning,
            stacklevel=3,
        )
        return

    # World- or group-writable is never accidental, and it is the tampering vector: any
    # local user can rewrite base_url and collect your key on the next call. No override.
    if mode & 0o022:
        raise ConfigError(
            "%s is writable by other users (mode %o). Anyone who can write this file can "
            "redirect your API key to a server they control. Fix with: chmod 600 %s"
            % (path, mode, path)
        )

    # Readable by others is a warning, not a refusal. `nano ~/.config/puntersedge/config`
    # under a default umask produces 0644, so refusing here would fire on the modal
    # first-time user — and the override they would then set becomes permanent.
    if mode & 0o044:
        warnings.warn(
            "%s is readable by other users (mode %o). Fix with: chmod 600 %s"
            % (path, mode, path),
            UserWarning,
            stacklevel=3,
        )


# ── reading ──────────────────────────────────────────────────────────────────────────
def load_config_file(path: PathLike, *, required: bool) -> Optional[Dict[str, Dict[str, str]]]:
    """Parse a config file into {section: {option: value}}, or None if absent.

    `required=True` turns absence into a hard error — that is the explicitly-configured
    path, where a typo must not silently fall through to a *different* key.

    "Present but unreadable" is never treated as "absent", at any tier. A permission error
    or a decoding error means your key is being ignored for a reason you can fix, and
    swallowing it converts a one-line local message into a server 401.
    """
    path = os.fspath(path)
    try:
        # fstat the descriptor we will actually read, rather than stat-then-open. The path
        # checked and the file read are otherwise not guaranteed to be the same object,
        # and fstat additionally validates the symlink TARGET rather than the link.
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        if required:
            raise ConfigError(
                "%s: no such file. (This path came from an explicit config_file argument "
                "or $%s, so it is not skipped silently.)" % (path, CONFIG_FILE_ENV)
            )
        return None
    except IsADirectoryError:
        raise ConfigError("%s: is a directory, not a config file." % (path,))
    except PermissionError as exc:
        raise ConfigError("%s: cannot be read (%s)." % (path, exc.strerror))

    try:
        st = os.fstat(fd)
        _check_mode(path, st)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        raise ConfigError(
            "%s: is not UTF-8 text. Expected an INI file with a [puntersedge] section."
            % (path,)
        )
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise

    parser = configparser.RawConfigParser()
    try:
        parser.read_string(text, source=path)
    except configparser.Error as exc:
        # Path and line number ONLY — never the offending source text. A parse error that
        # helpfully quotes the bad line publishes the API key to stderr and to CI logs.
        # Same class as the psycopg2 DSN-in-traceback rule.
        line = getattr(exc, "lineno", None) or (getattr(exc, "errors", None) or [(None,)])[0][0]
        where = "%s, line %s" % (path, line) if line else str(path)
        raise ConfigError(
            "%s: could not be parsed as INI (%s). The offending text is deliberately not "
            "shown here, because it may be your API key." % (where, type(exc).__name__)
        )

    unknown = set(parser.sections()) - ALLOWED_SECTIONS
    if unknown:
        # Refused rather than ignored, so `[sportsbet]` is a startup failure instead of a
        # natural next commit.
        for name in sorted(unknown):
            _check_boundary(path, name)
        raise ConfigError(
            "%s: unknown section(s) %s. A puntersedge config has exactly three sections: "
            "[%s], [%s] and [%s]." % (path, sorted(unknown), SECTION, ARB_SECTION,
                                      ALERT_SECTION)
        )

    out: Dict[str, Dict[str, str]] = {}
    for name in parser.sections():
        values = {}
        for option, raw in parser.items(name):
            _check_boundary(path, option)
            # Blank dropped HERE so file semantics match env semantics exactly: a bare
            # `api_key =` is unset, not an empty key.
            clean = _nonblank(raw)
            if clean is not None:
                values[option] = clean
        out[name] = values
    return out


# ── the chain ────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Resolved:
    """A value and where it came from. The source string is what makes a wrong key
    diagnosable without ever printing the key."""

    value: Optional[str]
    source: str


class ConfigChain:
    """Resolves settings across explicit / environment / file, PER VALUE.

    Per-value, not per-source, and that matters. The common real setup is base_url and
    timeout in the file with the key injected as an environment variable by CI. A
    per-source chain would let the environment win outright and silently discard the
    file's base_url — pointing the client at production when the file said staging.

    `env` and `config_file` are threaded in as parameters and nothing below this entry
    point ever reaches for `os.environ`. That is not style: a version of this that read
    the environment inline resolved its own precedence tests against the developer's real
    `~/.config/puntersedge/config`, reporting "no such file" for a file created two lines
    earlier in the test.
    """

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        config_file: Optional[PathLike] = None,
    ):
        self.env: Mapping[str, str] = os.environ if env is None else env
        explicit = config_file if config_file is not None else _nonblank(
            self.env.get(CONFIG_FILE_ENV)
        )
        self._explicit_path = explicit
        self._path: Optional[str] = (
            os.fspath(explicit) if explicit is not None else default_config_path(self.env)
        )
        self._loaded = False
        self._data: Optional[Dict[str, Dict[str, str]]] = None

    @property
    def path(self) -> Optional[str]:
        return self._path

    def _file(self) -> Optional[Dict[str, Dict[str, str]]]:
        if not self._loaded:
            self._loaded = True
            self._data = (
                None
                if self._path is None
                else load_config_file(self._path, required=self._explicit_path is not None)
            )
        return self._data

    def get(
        self,
        name: str,
        *,
        section: str = SECTION,
        explicit: Optional[str] = None,
        env_names: Sequence[str] = (),
    ) -> Resolved:
        """First non-blank of: explicit argument, each env name in order, then the file."""
        clean = _nonblank(explicit)
        if clean is not None:
            return Resolved(clean, "%s= argument" % name)
        for env_name in env_names:
            value = _nonblank(self.env.get(env_name))
            if value is not None:
                return Resolved(value, "$" + env_name)
        data = self._file()
        if data is not None:
            value = data.get(section, {}).get(name)
            if value is not None:
                return Resolved(value, "%s [%s] %s" % (self._path, section, name))
        return Resolved(None, "not found")

    def trace(
        self, name: str, *, section: str = SECTION, env_names: Sequence[str] = ()
    ) -> List[str]:
        """One human-readable line per tier: what it was, and what it actually reported.

        This is the payload of the terminal error. It collapses four indistinguishable
        failure states — never passed, empty variable, wrong path, wrong section — into
        one glance, and it does so WITHOUT printing any part of the key.
        """
        lines = ["%-52s — not passed" % ("1. " + name + "= argument")]
        n = 2
        for env_name in env_names:
            raw = self.env.get(env_name)
            if raw is None:
                state = "not set"
            elif not raw.strip():
                state = "set but EMPTY (treated as unset)"
            else:
                state = "set"
            lines.append("%-52s — %s" % ("%d. $%s" % (n, env_name), state))
            n += 1

        if self._path is None:
            lines.append(
                "%-52s — skipped: no HOME/APPDATA in the environment"
                % ("%d. default config file" % n)
            )
            return lines

        label = "%d. %s [%s] %s" % (n, self._path, section, name)
        try:
            data = self._file()
        except ConfigError as exc:
            lines.append("%-52s — %s" % (label, exc))
            return lines
        if data is None:
            lines.append("%-52s — no such file" % label)
        elif section not in data:
            found = sorted(data) or ["none"]
            lines.append(
                "%-52s — file read OK, no [%s] section (found: %s)"
                % (label, section, ", ".join(found))
            )
        elif name not in data[section]:
            found = sorted(data[section]) or ["none"]
            lines.append(
                "%-52s — file read OK, [%s] has no %s (found: %s)"
                % (label, section, name, ", ".join(found))
            )
        else:
            lines.append("%-52s — found" % label)
        return lines


def resolve_api_key(
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    config_file: Optional[PathLike] = None,
) -> str:
    """The API key, from the first source that has one. Raises `ApiKeyError` with a full
    trace when none does.

    An explicit `""` is a hard error rather than "walk the chain": an empty string almost
    always means the caller's own lookup came back empty, and quietly substituting a
    different key would transact under credentials they never chose.
    """
    if explicit is not None and not isinstance(explicit, str):
        raise ApiKeyError("api_key must be a string, got %s" % type(explicit).__name__)
    if explicit is not None and not explicit.strip():
        raise ApiKeyError(
            "api_key was passed as an empty string. If you meant to look it up from the "
            "environment or a config file, pass nothing at all instead."
        )

    chain = ConfigChain(env=env, config_file=config_file)
    env_names = (ENV_PREFIX + "API_KEY",)
    found = chain.get("api_key", explicit=explicit, env_names=env_names)
    if found.value is not None:
        return found.value

    path = chain.path or os.path.join("~", ".config", "puntersedge", "config")
    raise ApiKeyError(
        "No PuntersEdge API key found. Tried, in order:\n  "
        + "\n  ".join(chain.trace("api_key", env_names=env_names))
        + "\n\nFix either of:\n"
        "  export %sAPI_KEY=<your key>\n"
        "  printf '[%s]\\napi_key = <your key>\\n' > %s && chmod 600 %s\n"
        "\nFree key (1,500 credits/mo, no credit card):\n  %s"
        % (ENV_PREFIX, SECTION, path, path, SIGNUP_URL)
    )
