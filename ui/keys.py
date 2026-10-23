"""Idempotency keys derived from the user's intent.

The same intent always gives the same key: a second click, a rerun of the
script or a reloaded page sends the stored key again, and the server answers
with the stored result instead of acting twice. A different intent, such as
the same command on a newer workflow version, gets a new key.
"""

import hashlib

_ACTIONS = frozenset({"start", "check", "revise", "approve", "reject"})


def command_key(action: str, workflow_id: str, *parts: object) -> str:
    """Return a key of at most 60 safe characters for one command intent."""
    if action not in _ACTIONS:
        raise ValueError(f"Unknown command {action!r}.")
    text = "\x1f".join([action, workflow_id, *(str(part) for part in parts)])
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]
    return f"ui-{action}-{digest}"
