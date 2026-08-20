"""Validation of the initData a Telegram Mini App sends to the backend.

The page runs on the user's device, so nothing it claims can be trusted on its
own. Telegram signs initData with a key derived from the bot token; checking
that signature is what proves which user actually opened the app.
"""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 60 * 60


def verify(init_data: str) -> dict | None:
    """Return the parsed initData if it is genuine and fresh, else None."""
    if not init_data:
        return None

    fields = dict(parse_qsl(init_data))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        return None

    try:
        auth_age = time.time() - int(fields.get("auth_date", 0))
    except ValueError:
        return None
    if auth_age > MAX_AGE_SECONDS:
        return None

    try:
        fields["user"] = json.loads(fields.get("user", "{}"))
    except json.JSONDecodeError:
        return None

    return fields
