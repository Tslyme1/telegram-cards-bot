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


def get_chat_title(chat_id) -> str:
    return kv.get(_key("chattitle", chat_id)) or str(chat_id)


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


def give_green_card(chat_id, user_id, display_name: str, limit: int) -> tuple[str, int]:
    """Cancel one yellow card, or bank a green one against future yellows.

    Returns (outcome, count) where outcome is one of "yellow_removed" (count is
    the remaining yellows), "green_banked" or "green_full" (count is how many
    green cards the person now holds).
    """
    remember_participant(chat_id, user_id, display_name)

    yellow = int(kv.get(_key("yellow", chat_id, user_id)) or 0)
    if yellow > 0:
        yellow -= 1
        kv.set(_key("yellow", chat_id, user_id), yellow)
        return "yellow_removed", yellow

    stock = green_count(chat_id, user_id)
    if stock >= limit:
        return "green_full", stock

    stock += 1
    kv.set(_key("immunity", chat_id, user_id), stock)
    return "green_banked", stock


def add_red_card(chat_id, user_id, display_name: str) -> int:
    """Award a red card directly, without going through the yellow threshold."""
    remember_participant(chat_id, user_id, display_name)
    return int(kv.incr(_key("red", chat_id, user_id)))


def green_count(chat_id, user_id) -> int:
    return int(kv.get(_key("immunity", chat_id, user_id)) or 0)


def take_green_card(chat_id, user_id) -> tuple[bool, int]:
    """Spend one banked green card. Returns (spent, remaining)."""
    stock = green_count(chat_id, user_id)
    if stock <= 0:
        return False, 0

    stock -= 1
    if stock:
        kv.set(_key("immunity", chat_id, user_id), stock)
    else:
        kv.delete(_key("immunity", chat_id, user_id))
    return True, stock


def list_cards(chat_id) -> list[tuple[str, int, int, int]]:
    user_ids = kv.smembers(_key("users", chat_id))
    rows = []
    for uid in user_ids:
        name = kv.get(_key("name", chat_id, uid)) or str(uid)
        yellow = int(kv.get(_key("yellow", chat_id, uid)) or 0)
        red = int(kv.get(_key("red", chat_id, uid)) or 0)
        rows.append((name, yellow, red, green_count(chat_id, uid)))

    rows.sort(key=lambda row: (-row[2], -row[1], row[0].lower()))
    return rows


def _set_balance(chat_id, user_id, balance: int) -> None:
    """Store a balance. Deliberately without a TTL: it carries across days."""
    kv.set(_key("coins", chat_id, user_id), balance)


def get_kabankoins(chat_id, user_id, day_key: str, daily_amount: int) -> int:
    """Read the balance, topping it up first if today's top-up hasn't run yet.

    The balance itself persists across days — winnings stay won. What the new
    day does is lift anyone *below* the daily allowance back up to it (a debt
    included), and leave anyone above it untouched.

    The top-up is applied lazily, on the first read of the day, because the
    bot has no scheduled execution: it only ever reacts to incoming updates.
    Every other kabankoin function reads through here, so no path can spend or
    add against a stale, un-topped-up balance.
    """
    raw = kv.get(_key("coins", chat_id, user_id))
    balance = int(raw) if raw is not None else daily_amount

    if kv.get(_key("topup", chat_id, user_id)) == day_key:
        return balance

    if balance < daily_amount:
        balance = daily_amount
    _set_balance(chat_id, user_id, balance)
    kv.set(_key("topup", chat_id, user_id), day_key)
    return balance


def spend_kabankoins(chat_id, user_id, day_key: str, amount: int, daily_amount: int) -> tuple[bool, int]:
    """Try to deduct `amount`. Returns (spent, balance_after)."""
    balance = get_kabankoins(chat_id, user_id, day_key, daily_amount)
    if balance < amount:
        return False, balance
    balance -= amount
    _set_balance(chat_id, user_id, balance)
    return True, balance


def spend_kabankoins_on_credit(
    chat_id, user_id, day_key: str, amount: int, daily_amount: int, floor: int
) -> tuple[bool, int, int]:
    """Deduct `amount`, allowing the balance to go negative down to `floor`.

    Unlike spend_kabankoins, this only refuses when the debt floor itself
    would be breached, not merely for lack of funds. Returns (spent,
    balance_before, balance_after) — on refusal, before and after are both
    the unchanged current balance.
    """
    before = get_kabankoins(chat_id, user_id, day_key, daily_amount)
    after = before - amount
    if after < floor:
        return False, before, before
    _set_balance(chat_id, user_id, after)
    return True, before, after


def reset_kabankoins(chat_id, user_id, day_key: str) -> None:
    """Drop the stored balance, so the next read falls back to the daily default."""
    kv.delete(_key("coins", chat_id, user_id))
    kv.delete(_key("topup", chat_id, user_id))


def add_kabankoins(chat_id, user_id, day_key: str, amount: int, daily_amount: int) -> int:
    balance = get_kabankoins(chat_id, user_id, day_key, daily_amount) + amount
    _set_balance(chat_id, user_id, balance)
    return balance


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


def set_temp_title(chat_id, user_id, expires_at: int) -> None:
    """Record a purchased member tag, so a lazy sweep can find and clear it later."""
    kv.sadd(_key("titled", chat_id), str(user_id))
    kv.set(_key("titleexp", chat_id, user_id), expires_at)


def get_title_expiry(chat_id, user_id) -> int | None:
    raw = kv.get(_key("titleexp", chat_id, user_id))
    return int(raw) if raw is not None else None


def clear_temp_title(chat_id, user_id) -> None:
    kv.delete(_key("titleexp", chat_id, user_id))
    kv.srem(_key("titled", chat_id), str(user_id))


def list_titled(chat_id) -> list[str]:
    """User ids with a currently tracked temporary member tag in this chat."""
    return kv.smembers(_key("titled", chat_id))


def set_state(user_id, state: dict) -> None:
    kv.set(_key("state", user_id), json.dumps(state), ex=STATE_TTL_SECONDS)


def get_state(user_id) -> dict | None:
    raw = kv.get(_key("state", user_id))
    return json.loads(raw) if raw else None


def clear_state(user_id) -> None:
    kv.delete(_key("state", user_id))
