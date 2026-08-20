"""A string that does not print itself.

The API key is the only secret this package handles. It leaks not through anything dramatic
but through the ordinary machinery of Python debugging: frame locals in a traceback, a
`vars()` dump, a debugger's variables pane, a pickle sent to a worker process. All of those
call `repr()`. A plain `str` answers that question honestly and puts the key in a log.

Verified before this existed (sentinel key, this repo, 2026-08-20):
  * `pytest` with NO flags printed the key twice on a connection failure
  * `pytest --showlocals` printed it nine times
  * `vars(client)` and `pickle.dumps(client)` both contained it

`reveal()` is deliberately ugly and deliberately the only way out. Every call site is a
place a secret escapes, so they should be greppable and countable — there are two.
"""
from __future__ import annotations


class Secret:
    """Wraps a secret string so that repr/str/format never disclose it.

    Not a security boundary — anything with the object can call `reveal()`. It is a
    guard against ACCIDENTAL disclosure, which is the entire realistic threat here.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str):
            raise TypeError("Secret takes a str, got %s" % type(value).__name__)
        self._value = value

    def reveal(self) -> str:
        """The raw value. Call sites are audited — do not add one casually."""
        return self._value

    # Every stringification path, closed. `__format__` matters as much as the others:
    # f"{key}" does not go through __str__ when __format__ is defined, and an f-string in
    # a log line is the single most likely way this ends up on disk.
    def __repr__(self) -> str:
        return "<Secret hidden>"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return self.__repr__()

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    # Serialisation is how a secret travels to a place nobody audited: a multiprocessing
    # queue, a joblib cache, a crash dump. Refuse loudly instead of silently exporting it.
    def __reduce__(self):
        raise TypeError(
            "A PuntersEdge API key cannot be pickled. Pass the key to each process and "
            "construct a client there, rather than sending a constructed one."
        )

    def __getstate__(self):
        raise TypeError("A PuntersEdge API key cannot be serialised.")

    def __deepcopy__(self, memo):
        # Deep-copying a client should not silently mint a second copy of the key in a
        # place the original audit never looked. Sharing the immutable wrapper is correct.
        return self
