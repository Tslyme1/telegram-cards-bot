"""Card storage backed by Vercel KV, for the webhook (Vercel) deployment."""

import json

import kv

# How long an unfinished "give a card" dialog in the bot's DM stays valid.
STATE_TTL_SECONDS = 600


def _key(prefix: str, chat_id, user_id=None) -> str:
    if user_id is None:
        return f"{prefix}:{chat_id}"
    return f"{prefix}:{chat_id}:{user_id}"


def remember_chat(chat_id, title: str) -> None:
    kv.set(_key("chattitle", chat_id), title)


def remember_participant(chat_id, user_id, display_name: str) -> None:
    """Record someone as a known participant of a chat.

    Telegram gives bots no way to list a group's members, so the roster shown
    in the card dialog can only be built from users the bot has actually seen.
    """
    kv.sadd(_key("users", chat_id), str(user_id))
    kv.set(_key("name", chat_id, user_id), display_name)
    kv.sadd(_key("chats", user_id), str(chat_id))


def get_name(chat_id, user_id) -> str | None:
    return kv.get(_key("name", chat_id, user_id))


def list_participants(chat_id) -> list[tuple[str, str]]:
    """Return [(user_id, display_name)] of known participants, sorted by name."""
    participants = [
        (uid, kv.get(_key("name", chat_id, uid)) or str(uid))
        for uid in kv.smembers(_key("users", chat_id))
    ]
    participants.sort(key=lambda row: row[1].lower())
    return participants


def list_user_chats(user_id) -> list[tuple[str, str]]:
    """Return [(chat_id, title)] of chats where the bot has seen this user."""
    chats = [
        (chat_id, kv.get(_key("chattitle", chat_id)) or str(chat_id))
        for chat_id in kv.smembers(_key("chats", user_id))
    ]
    chats.sort(key=lambda row: row[1].lower())
    return chats


def add_yellow_card(chat_id, user_id, display_name: str, threshold: int) -> tuple[int, int]:
    """Add one yellow card. If the count reaches threshold, reset yellow to 0 and add one red card.

    Returns (yellow_count_after, red_count_after).
    """
    remember_participant(chat_id, user_id, display_name)

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


def next_mute_seconds(chat_id, user_id, day_key: str, ladder: list[int]) -> tuple[int, int]:
    """Count today's red cards for someone and return how long to mute them.

    The counter is stored per day, so the ladder starts over every day on its
    own — no scheduled cleanup needed.

    Returns (mute_seconds, red_cards_today).
    """
    key = f"{_key('mutes', chat_id, user_id)}:{day_key}"
    count = kv.incr(key)
    kv.expire(key, 2 * 24 * 60 * 60)
    return ladder[min(count, len(ladder)) - 1], count


def set_state(user_id, state: dict) -> None:
    kv.set(_key("state", user_id), json.dumps(state), ex=STATE_TTL_SECONDS)


def get_state(user_id) -> dict | None:
    raw = kv.get(_key("state", user_id))
    return json.loads(raw) if raw else None


def clear_state(user_id) -> None:
    kv.delete(_key("state", user_id))
