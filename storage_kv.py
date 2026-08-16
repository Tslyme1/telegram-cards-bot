"""Card storage backed by Vercel KV, for the webhook (Vercel) deployment."""

import kv


def _key(prefix: str, chat_id, user_id=None) -> str:
    if user_id is None:
        return f"{prefix}:{chat_id}"
    return f"{prefix}:{chat_id}:{user_id}"


def add_yellow_card(chat_id, user_id, display_name: str, threshold: int) -> tuple[int, int]:
    """Add one yellow card. If the count reaches threshold, reset yellow to 0 and add one red card.

    Returns (yellow_count_after, red_count_after).
    """
    kv.sadd(_key("users", chat_id), str(user_id))
    kv.set(_key("name", chat_id, user_id), display_name)

    yellow = kv.incr(_key("yellow", chat_id, user_id))
    red = int(kv.get(_key("red", chat_id, user_id)) or 0)

    if yellow >= threshold:
        kv.set(_key("yellow", chat_id, user_id), 0)
        red = kv.incr(_key("red", chat_id, user_id))
        yellow = 0

    return yellow, red


def list_cards(chat_id) -> list[tuple[str, int, int]]:
    user_ids = kv.smembers(_key("users", chat_id))
    rows = []
    for uid in user_ids:
        name = kv.get(_key("name", chat_id, uid)) or str(uid)
        yellow = int(kv.get(_key("yellow", chat_id, uid)) or 0)
        red = int(kv.get(_key("red", chat_id, uid)) or 0)
        rows.append((name, yellow, red))

    rows.sort(key=lambda row: (-row[2], -row[1], row[0].lower()))
    return rows
