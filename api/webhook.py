import itertools
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request  # noqa: E402

import kv  # noqa: E402
import storage_kv as storage  # noqa: E402
import telegram_api as tg  # noqa: E402
from config import (  # noqa: E402
    CASINO_CARDS_SPIN_COST,
    CASINO_COINS_BET_MAX,
    CASINO_COINS_BET_MIN,
    CASINO_COOLDOWN_SECONDS,
    CASINO_LOSING_COMBOS,
    CASINO_OUTCOME_WEIGHTS,
    CASINO_REEL_SYMBOLS,
    CASINO_SLOTS,
    CASINO_SYMBOLS,
    DAY_RESET_UTC_OFFSET_HOURS,
    GIVE_COOLDOWN_SECONDS,
    GREEN_COOLDOWN_SECONDS,
    GREEN_IMMUNITY_LIMIT,
    KABANKOIN_DAILY_AMOUNT,
    KABANKOIN_DEBT_BAN_LEVEL,
    KABANKOIN_DEBT_BAN_SECONDS,
    KABANKOIN_DEBT_RED_LEVEL,
    KABANKOIN_DEBT_YELLOW_LEVEL,
    KABANKOIN_PAYOUT_TIERS,
    MUTE_LADDER_SECONDS,
    NICKNAME_MAX_LENGTH,
    NICKNAME_PRICE_TIERS,
    YELLOW_THRESHOLD,
)

app = Flask(__name__)

GROUP_TYPES = ("group", "supergroup")

CANCEL_BUTTON = {"text": "Отмена", "callback_data": "cancel"}

CARD_NAME = {"yellow": "жёлтую", "green": "зелёную", "casino": "случайную"}
CARD_NAME_NOMINATIVE = {"yellow": "жёлтая", "green": "зелёная", "red": "красная"}
CARD_EMOJI = {"yellow": "🟨", "green": "🟩", "red": "🟥", "casino": "🎰"}

KABANKOIN_EMOJI = "🪙"

COOLDOWN_SUBJECT = {
    "yellow": "жёлтой карточкой",
    "green": "зелёной карточкой",
    "casino": "прокруткой казино",
}
COOLDOWN_FOR_KIND = {
    "yellow": GIVE_COOLDOWN_SECONDS,
    "green": GREEN_COOLDOWN_SECONDS,
    "casino": CASINO_COOLDOWN_SECONDS,
}


def display_name(user: dict) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    return name or str(user["id"])


def cooldown_key(chat_id, giver_id, kind: str = "yellow") -> str:
    """Key for one person's cooldown on one kind of card.

    Separate per kind, so handing out a green card does not block a yellow one.

    The configured duration is part of the key on purpose: changing a cooldown
    in config.py moves every waiter to a fresh key, so nobody stays stuck on a
    countdown from the old setting. The stale keys expire on their own.
    """
    return f"cooldown:{kind}:{COOLDOWN_FOR_KIND[kind]}:{chat_id}:{giver_id}"


def current_day_key() -> str:
    """Today's date in the timezone where the mute ladder resets."""
    tz = timezone(timedelta(hours=DAY_RESET_UTC_OFFSET_HOURS))
    return datetime.now(tz).date().isoformat()


def seconds_until_day_reset() -> int:
    tz = timezone(timedelta(hours=DAY_RESET_UTC_OFFSET_HOURS))
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - now).total_seconds())


def get_kabankoins(chat_id, user_id) -> int:
    return storage.get_kabankoins(chat_id, user_id, current_day_key(), KABANKOIN_DAILY_AMOUNT)


def insufficient_balance_text(balance: int) -> str:
    return (
        f"{KABANKOIN_EMOJI} Не хватает кабанкоинов (баланс: {balance}). "
        f"Обновится через {format_duration(seconds_until_day_reset())}."
    )


def debt_limit_text() -> str:
    return (
        f"{KABANKOIN_EMOJI} Долг уже {KABANKOIN_DEBT_BAN_LEVEL} — играть в казино нельзя, "
        "пока баланс не подрастёт."
    )


def roll_kabankoin_payout(bet: int) -> int:
    """Pick a payout tier, weighted by bet, then roll a value inside it."""
    span = CASINO_COINS_BET_MAX - CASINO_COINS_BET_MIN
    t = (bet - CASINO_COINS_BET_MIN) / span if span else 0
    tiers = [(lo, hi) for lo, hi, _, _ in KABANKOIN_PAYOUT_TIERS]
    weights = [w_min + (w_max - w_min) * t for _, _, w_min, w_max in KABANKOIN_PAYOUT_TIERS]
    lo, hi = random.choices(tiers, weights=weights)[0]
    return random.randint(lo, hi)


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def format_duration(seconds: int) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} {_plural(hours, 'час', 'часа', 'часов')}"
    if seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} {_plural(minutes, 'минуту', 'минуты', 'минут')}"
    return f"{seconds} {_plural(seconds, 'секунду', 'секунды', 'секунд')}"


def bot_username() -> str:
    if not hasattr(bot_username, "_cached"):
        try:
            bot_username._cached = tg.get_me().get("username", "")
        except Exception:
            bot_username._cached = ""
    return bot_username._cached


def _remember(message: dict) -> None:
    """Keep the roster of who is in which chat up to date."""
    chat = message["chat"]
    if chat.get("type") not in GROUP_TYPES:
        return

    storage.remember_chat(chat["id"], chat.get("title") or str(chat["id"]))
    sweep_expired_titles(chat["id"])

    seen = [message.get("from"), *message.get("new_chat_members", [])]
    # Mentions carry the full user object, so they name people who never posted.
    for entity in [*message.get("entities", []), *message.get("caption_entities", [])]:
        if entity.get("type") == "text_mention" and entity.get("user"):
            seen.append(entity["user"])

    for user in seen:
        if user and not user.get("is_bot"):
            storage.remember_participant(chat["id"], user["id"], display_name(user))


def _remember_membership(event: dict) -> None:
    """Record members from chat_member updates.

    These arrive for every join/leave/status change - including people who
    never write anything - but only if the webhook subscribes to them.
    """
    chat = event.get("chat") or {}
    if chat.get("type") not in GROUP_TYPES:
        return

    storage.remember_chat(chat["id"], chat.get("title") or str(chat["id"]))

    member = event.get("new_chat_member") or {}
    user = member.get("user")
    if not user or user.get("is_bot"):
        return

    if member.get("status") in tg.MEMBER_STATUSES:
        storage.remember_participant(chat["id"], user["id"], display_name(user))


def sync_administrators(chat_id) -> None:
    """Pull in admins, who often never post but should still be listed."""
    try:
        for member in tg.get_chat_administrators(chat_id):
            user = member.get("user") or {}
            if user and not user.get("is_bot"):
                storage.remember_participant(chat_id, user["id"], display_name(user))
    except Exception:
        app.logger.exception("Failed to sync administrators for %s", chat_id)


def sweep_expired_titles(chat_id) -> None:
    """Best-effort demotion of expired badges, checked on chat activity.

    There is no scheduled execution anywhere in this bot — it only reacts to
    incoming updates — so an expired badge is only caught the next time
    someone posts in that chat, not to the second.
    """
    now = int(time.time())
    for uid in storage.list_titled(chat_id):
        expiry = storage.get_title_expiry(chat_id, uid)
        if expiry is not None and expiry > now:
            continue
        if expiry is not None:
            try:
                tg.demote_chat_member(chat_id, uid)
            except Exception:
                app.logger.exception("Failed to demote expired title for %s in %s", uid, chat_id)
        storage.clear_temp_title(chat_id, uid)


def send_to_dm(giver: dict, group_chat_id, text: str, keyboard=None):
    """Send something to one person privately, telling the group if we can't."""
    try:
        return tg.send_message(giver["id"], text, reply_markup=keyboard)
    except Exception:
        username = bot_username()
        hint = f" — @{username}" if username else ""
        tg.send_message(
            group_chat_id,
            f"{display_name(giver)}, автомат я открываю в личных сообщениях. "
            f"Напишите мне в личку{hint}, нажмите «Старт» и повторите команду.",
        )
        return None


# --- the card dialog, which runs in the bot's private chat -------------------


def start_give_flow(user_id, dm_chat_id, kind: str) -> None:
    chats = storage.list_user_chats(user_id)

    if not chats:
        tg.send_message(
            dm_chat_id,
            "Я пока не знаю ни одного чата, где вы состоите.\n"
            f"Напишите что-нибудь в группе, где я работаю, и повторите {COMMAND_FOR_KIND[kind]}.",
        )
        return

    if len(chats) == 1:
        ask_for_target(dm_chat_id, None, chats[0][0], user_id, kind)
        return

    keyboard = [
        [{"text": title, "callback_data": f"chat:{kind}:{chat_id}"}] for chat_id, title in chats
    ]
    keyboard.append([CANCEL_BUTTON])
    tg.send_message(
        dm_chat_id,
        f"В каком чате выдать {CARD_NAME[kind]} карточку?",
        reply_markup={"inline_keyboard": keyboard},
    )


def ask_for_target(dm_chat_id, message_id, chat_id, giver_id=None, kind: str = "yellow") -> None:
    sync_administrators(chat_id)
    participants = [
        (uid, name)
        for uid, name in storage.list_participants(chat_id)
        if giver_id is None or str(uid) != str(giver_id)
    ]

    if not participants:
        text = (
            "В этом чате я пока никого не видел.\n"
            "Участники появятся в списке после того, как напишут что-нибудь в чате."
        )
        keyboard = None
    else:
        text = f"Кому выдать {CARD_NAME[kind]} карточку?"
        keyboard = {
            "inline_keyboard": [
                *[
                    [{"text": name, "callback_data": f"user:{kind}:{chat_id}:{uid}"}]
                    for uid, name in participants
                ],
                [CANCEL_BUTTON],
            ]
        }

    if message_id is None:
        tg.send_message(dm_chat_id, text, reply_markup=keyboard)
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)


def ask_for_reason(dm_chat_id, message_id, chat_id, target_id, giver_id, kind: str) -> None:
    storage.set_state(
        giver_id,
        {
            "step": "reason",
            "kind": kind,
            "chat_id": str(chat_id),
            "target_id": str(target_id),
        },
    )

    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    keyboard = {
        "inline_keyboard": [
            [{"text": "Без причины", "callback_data": "noreason"}],
            [CANCEL_BUTTON],
        ]
    }
    tg.edit_message_text(
        dm_chat_id,
        message_id,
        f"{CARD_EMOJI[kind]} Кому: {target_name}\n\nОтправьте причину сообщением "
        "или нажмите «Без причины».",
        reply_markup=keyboard,
    )


def finish_give(
    giver: dict, ack_chat_id, chat_id, target_id, reason: str | None, kind: str = "yellow"
) -> None:
    """Award the card in `chat_id`; progress and errors go to `ack_chat_id`."""
    storage.clear_state(giver["id"])

    giver_name = display_name(giver)
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    given_from_group = str(ack_chat_id) == str(chat_id)

    if not tg.is_chat_member(chat_id, giver["id"]):
        tg.send_message(ack_chat_id, "Вы больше не состоите в этом чате — карточка не выдана.")
        return

    key = cooldown_key(chat_id, giver["id"], kind)
    remaining = kv.ttl(key)
    if remaining > 0:
        tg.send_message(
            ack_chat_id,
            f"Подождите ещё {format_duration(remaining)} перед следующей "
            f"{COOLDOWN_SUBJECT[kind]}.",
        )
        return
    kv.set(key, "1", ex=COOLDOWN_FOR_KIND[kind])

    apply_card(
        giver_name, ack_chat_id, chat_id, target_id, target_name, kind, _details(giver_name, reason)
    )


def _details(giver_name: str, reason: str | None, label: str = "Выдал") -> str:
    details = f"\n{label}: {giver_name}"
    if reason:
        details += f"\nПричина: {reason}"
    return details


def apply_card(
    giver_name,
    ack_chat_id,
    chat_id,
    target_id,
    target_name,
    kind: str,
    details: str,
    header: str = "",
    backfired: bool = False,
) -> None:
    """Apply one resolved card, announcing it in `chat_id`."""
    given_from_group = str(ack_chat_id) == str(chat_id)

    if kind == "green":
        _give_green(
            chat_id, ack_chat_id, target_id, target_name, details, given_from_group, header
        )
        return

    if kind == "red":
        red = storage.add_red_card(chat_id, target_id, target_name)
        _award_red(chat_id, target_id, target_name, red, details, header)
        if not given_from_group:
            tg.send_message(
                ack_chat_id,
                "Осечка — красная карточка досталась вам."
                if backfired
                else f"Готово, красная карточка: {target_name}.",
            )
        return

    spent, greens_left = storage.take_green_card(chat_id, target_id)
    if spent:
        left = f" Зелёных осталось: {greens_left}." if greens_left else ""
        tg.send_message(
            chat_id,
            f"{header}🟨 {target_name} получает жёлтую карточку, тратится одна зелёная."
            f"{left}{details}",
        )
        if not given_from_group:
            tg.send_message(ack_chat_id, f"{target_name} использовал зелёную карточку.")
        return

    yellow, red = storage.add_yellow_card(chat_id, target_id, target_name, YELLOW_THRESHOLD)

    if yellow == 0 and red > 0:
        _award_red(chat_id, target_id, target_name, red, details, header)
    else:
        tg.send_message(
            chat_id,
            f"{header}🟨 {target_name} получает жёлтую карточку "
            f"({yellow}/{YELLOW_THRESHOLD}).{details}",
        )

    if not given_from_group:
        tg.send_message(ack_chat_id, f"Готово, карточка выдана: {target_name}.")


def apply_debt_penalty(giver: dict, chat_id, before: int, after: int) -> None:
    """Casino bets can be taken on credit; crossing a debt level punishes the spinner.

    Only the most severe level newly crossed by this one spend fires — each
    level is checked in order and returns, so a spin can't trigger two levels
    at once, and a level already crossed by an earlier spin today is skipped
    (`before` is no longer above it).
    """
    giver_name = display_name(giver)
    storage.remember_participant(chat_id, giver["id"], giver_name)
    header = f"💸 {giver_name} залезает в долг ({after} {KABANKOIN_EMOJI}) — "

    if before > KABANKOIN_DEBT_BAN_LEVEL and after <= KABANKOIN_DEBT_BAN_LEVEL:
        until = int(time.time()) + KABANKOIN_DEBT_BAN_SECONDS
        try:
            tg.restrict_chat_member(chat_id, giver["id"], until)
            tg.send_message(chat_id, f"{header}бан на {format_duration(KABANKOIN_DEBT_BAN_SECONDS)}.")
        except Exception:
            tg.send_message(
                chat_id, f"{header}бан не удался: {_mute_failure_reason(chat_id, giver['id'])}."
            )
        return

    if before > KABANKOIN_DEBT_RED_LEVEL and after <= KABANKOIN_DEBT_RED_LEVEL:
        apply_card(giver_name, chat_id, chat_id, giver["id"], giver_name, "red", "", header)
        return

    if before > KABANKOIN_DEBT_YELLOW_LEVEL and after <= KABANKOIN_DEBT_YELLOW_LEVEL:
        apply_card(giver_name, chat_id, chat_id, giver["id"], giver_name, "yellow", "", header)
        return


# --- the slot machine behind /casino ----------------------------------------


def slots_keyboard(symbols: list[str]) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": symbol, "callback_data": f"sl:{index}"}
                for index, symbol in enumerate(symbols)
            ],
            [CANCEL_BUTTON],
        ]
    }


def slots_text(giver_name: str, target_name: str, picked: list[str]) -> str:
    reels = " ".join(picked + ["⬜"] * (CASINO_SLOTS - len(picked)))
    return (
        f"🎰 {giver_name} крутит казино на {target_name}\n\n"
        f"{reels}\n\n"
        f"Символ {len(picked) + 1} из {CASINO_SLOTS} — выбирайте."
    )


def begin_casino_cards(giver: dict, chat_id, target_id, reason, dm_chat_id, message_id) -> None:
    """Spend the spin cost and open the slot machine, editing the picker in place."""
    giver_name = display_name(giver)
    target_name = storage.get_name(chat_id, target_id) or str(target_id)

    spent, before, after = storage.spend_kabankoins_on_credit(
        chat_id,
        giver["id"],
        current_day_key(),
        CASINO_CARDS_SPIN_COST,
        KABANKOIN_DAILY_AMOUNT,
        KABANKOIN_DEBT_BAN_LEVEL,
    )
    if not spent:
        tg.edit_message_text(dm_chat_id, message_id, debt_limit_text())
        storage.clear_state(giver["id"])
        return
    if after < 0:
        apply_debt_penalty(giver, chat_id, before, after)

    # Each spin runs on its own reels, drawn from the full symbol set.
    symbols = random.sample(CASINO_SYMBOLS, CASINO_REEL_SYMBOLS)
    every_combo = ["".join(c) for c in itertools.product(symbols, repeat=CASINO_SLOTS)]

    tg.edit_message_text(
        dm_chat_id,
        message_id,
        slots_text(giver_name, target_name, []),
        reply_markup=slots_keyboard(symbols),
    )

    storage.set_state(
        giver["id"],
        {
            "step": "slots",
            "chat_id": str(chat_id),
            "target_id": str(target_id),
            "reason": reason,
            "symbols": symbols,
            "picked": [],
            # Ties the state to this one machine, so taps on any other
            # keyboard cannot be resolved against it.
            "message_id": message_id,
            # Drawn per spin and never revealed, so the odds stay honest even
            # though the player picks the symbols by hand.
            "losing": random.sample(every_combo, CASINO_LOSING_COMBOS),
        },
    )


def pick_slot_symbol(giver: dict, state: dict, dm_chat_id, message_id, symbol_index: int) -> None:
    giver_name = display_name(giver)
    chat_id = state["chat_id"]
    target_id = state["target_id"]
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    symbols = state["symbols"]

    picked = state["picked"] + [symbols[symbol_index]]

    if len(picked) < CASINO_SLOTS:
        state["picked"] = picked
        storage.set_state(giver["id"], state)
        tg.edit_message_text(
            dm_chat_id,
            message_id,
            slots_text(giver_name, target_name, picked),
            reply_markup=slots_keyboard(symbols),
        )
        return

    resolve_slots(giver, state, dm_chat_id, message_id, picked, chat_id, target_id, target_name)


def resolve_slots(
    giver, state, ack_chat_id, message_id, picked, chat_id, target_id, target_name
) -> None:
    storage.clear_state(giver["id"])

    # The outcome follows as its own message, so the machine just stops here.
    tg.edit_message_text(ack_chat_id, message_id, f"🎰 {' '.join(picked)}")
    resolve_spin(giver, state, picked, chat_id, target_id, target_name)


def resolve_spin(giver, session, picked, chat_id, target_id, target_name) -> None:
    """Announce a finished spin in the chat, wherever it was played."""
    giver_name = display_name(giver)
    backfired = "".join(picked) in session["losing"]

    header = (
        f"🎰 {giver_name} крутит казино на {target_name}\n"
        f"Комбинация: {' '.join(picked)}\n"
    )
    # The header already names the spinner, so the card line below carries only
    # a reason, if one was given.
    reason = session.get("reason")
    details = f"\nПричина: {reason}" if reason else ""

    if backfired:
        storage.remember_participant(chat_id, giver["id"], giver_name)
        header += "Осечка!\n"
        apply_card(
            giver_name,
            chat_id,
            chat_id,
            giver["id"],
            giver_name,
            "red",
            details,
            header,
            backfired=True,
        )
        return

    kind = random.choices(
        list(CASINO_OUTCOME_WEIGHTS), weights=list(CASINO_OUTCOME_WEIGHTS.values())
    )[0]
    apply_card(
        giver_name,
        chat_id,
        chat_id,
        target_id,
        target_name,
        kind,
        details,
        header,
    )


def _award_red(chat_id, target_id, target_name, red: int, details: str, header: str = "") -> None:
    """Announce a red card and mute for however long the day's ladder says."""
    mute_seconds, reds_today = storage.next_mute_seconds(
        chat_id, target_id, current_day_key(), MUTE_LADDER_SECONDS
    )
    if reds_today > 1:
        details = f"\nКрасная карточка №{reds_today} за сегодня{details}"

    until = int(time.time()) + mute_seconds
    try:
        tg.restrict_chat_member(chat_id, target_id, until)
        tg.send_message(
            chat_id,
            f"{header}🟥 {target_name} получает красную карточку (всего красных: {red}) "
            f"и заглушен в чате на {format_duration(mute_seconds)}.{details}",
        )
    except Exception:
        tg.send_message(
            chat_id,
            f"{header}🟥 {target_name} получает красную карточку (всего красных: {red}), "
            f"но заглушить не удалось: {_mute_failure_reason(chat_id, target_id)}.{details}",
        )


def _mute_failure_reason(chat_id, target_id) -> str:
    """Explain a failed mute, since the usual advice does not fit every case."""
    status = tg.get_chat_member_status(chat_id, target_id)

    if status == "creator":
        return (
            "владельца чата не может ограничить никто, включая ботов — "
            "это ограничение Telegram, правами оно не лечится"
        )
    if status == "administrator":
        return (
            "администратора чата бот заглушить не может — "
            "снимите с него права администратора"
        )
    return (
        "дайте боту права администратора с включённым правом "
        "«Блокировка пользователей»"
    )


def _give_green(
    chat_id, ack_chat_id, target_id, target_name, details, given_from_group, header: str = ""
) -> None:
    outcome, count = storage.give_green_card(
        chat_id, target_id, target_name, GREEN_IMMUNITY_LIMIT
    )

    if outcome == "yellow_removed":
        text = (
            f"🟩 {target_name} получает зелёную карточку — "
            f"снята одна жёлтая ({count}/{YELLOW_THRESHOLD}).{details}"
        )
    elif outcome == "green_banked":
        text = (
            f"🟩 {target_name} получает зелёную карточку — снимать нечего, она "
            f"погасит будущую жёлтую. В запасе: {count}/{GREEN_IMMUNITY_LIMIT}.{details}"
        )
    else:
        text = (
            f"🟩 У {target_name} уже максимум зелёных карточек "
            f"({GREEN_IMMUNITY_LIMIT}) — больше не накопить.{details}"
        )

    tg.send_message(chat_id, header + text)
    if not given_from_group:
        tg.send_message(ack_chat_id, f"Готово, зелёная карточка: {target_name}.")


def show_group_picker(chat_id, giver: dict, reason: str | None, kind: str = "yellow") -> None:
    """Offer the chat's participants to the caller, privately.

    Telegram has no per-user message inside a group, so the list goes to the
    caller's private chat; the group only ever sees the result.
    """
    sync_administrators(chat_id)
    participants = [
        (uid, name)
        for uid, name in storage.list_participants(chat_id)
        if str(uid) != str(giver["id"])
    ]

    if not participants:
        tg.send_message(
            chat_id,
            "Я пока никого не видел в этом чате. Ответьте командой на сообщение участника "
            "или подождите, пока участники что-нибудь напишут.",
        )
        return

    prompt = f"Чат: {storage.get_chat_title(chat_id)}\nКому выдать {CARD_NAME[kind]} карточку?"
    if reason:
        prompt += f"\nПричина: {reason}"

    sent = send_to_dm(
        giver,
        chat_id,
        prompt,
        {
            "inline_keyboard": [
                *[
                    [{"text": name, "callback_data": f"gu:{kind}:{chat_id}:{uid}"}]
                    for uid, name in participants
                ],
                [CANCEL_BUTTON],
            ]
        },
    )
    if sent is None:
        return

    # The reason can be longer than callback_data allows, so it waits in the
    # state until the giver taps a name.
    storage.set_state(
        giver["id"],
        {
            "step": "pick",
            "kind": kind,
            "chat_id": str(chat_id),
            "reason": reason,
            "message_id": sent.get("message_id"),
        },
    )


# --- /casino: type picker, then either the card slots above or the coin bet -


def start_casino_flow(giver: dict, dm_chat_id, target_id=None, reason=None) -> None:
    """Entry point for /casino: pick a chat first if the caller is in several."""
    chats = storage.list_user_chats(giver["id"])
    if not chats:
        tg.send_message(
            dm_chat_id,
            "Я пока не знаю ни одного чата, где вы состоите.\n"
            f"Напишите что-нибудь в группе, где я работаю, и повторите {CASINO_COMMAND}.",
        )
        return

    if len(chats) == 1:
        open_casino_type_picker(giver, dm_chat_id, chats[0][0], None, target_id, reason)
        return

    keyboard = [[{"text": title, "callback_data": f"cchat:{cid}"}] for cid, title in chats]
    keyboard.append([CANCEL_BUTTON])
    sent = tg.send_message(
        dm_chat_id, "В каком чате играть в казино?", reply_markup={"inline_keyboard": keyboard}
    )
    storage.set_state(
        giver["id"],
        {"step": "casino_chat", "reason": reason, "message_id": sent.get("message_id")},
    )


def open_casino_type_picker(giver, dm_chat_id, chat_id, message_id, target_id, reason) -> None:
    balance = get_kabankoins(chat_id, giver["id"])
    text = (
        f"{KABANKOIN_EMOJI} Баланс: {balance}/{KABANKOIN_DAILY_AMOUNT}\n"
        "Какое казино крутим?"
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎴 Выдать карточку", "callback_data": "ctype:cards"}],
            [{"text": f"{KABANKOIN_EMOJI} Испытать удачу", "callback_data": "ctype:coins"}],
            [CANCEL_BUTTON],
        ]
    }

    if message_id is None:
        sent = send_to_dm(giver, chat_id, text, keyboard)
        if sent is None:
            return
        message_id = sent.get("message_id")
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)

    storage.set_state(
        giver["id"],
        {
            "step": "casino_type",
            "chat_id": str(chat_id),
            "target_id": str(target_id) if target_id is not None else None,
            "reason": reason,
            "message_id": message_id,
        },
    )


def show_casino_target_picker(giver_id, dm_chat_id, message_id, chat_id, reason) -> None:
    sync_administrators(chat_id)
    participants = [
        (uid, name)
        for uid, name in storage.list_participants(chat_id)
        if str(uid) != str(giver_id)
    ]

    if not participants:
        tg.edit_message_text(
            dm_chat_id,
            message_id,
            "Я пока никого не видел в этом чате. Ответьте командой на сообщение "
            "участника или подождите, пока участники что-нибудь напишут.",
        )
        storage.clear_state(giver_id)
        return

    tg.edit_message_text(
        dm_chat_id,
        message_id,
        "На кого крутим?",
        reply_markup={
            "inline_keyboard": [
                *[[{"text": name, "callback_data": f"cuser:{uid}"}] for uid, name in participants],
                [CANCEL_BUTTON],
            ]
        },
    )
    storage.set_state(
        giver_id,
        {"step": "casino_pick", "chat_id": str(chat_id), "reason": reason, "message_id": message_id},
    )


def show_bet_picker(giver_id, dm_chat_id, message_id, chat_id, reason) -> None:
    balance = get_kabankoins(chat_id, giver_id)
    # Betting can dip into debt, but never past the floor in one bet.
    max_bet = min(CASINO_COINS_BET_MAX, balance - KABANKOIN_DEBT_BAN_LEVEL)
    if max_bet < CASINO_COINS_BET_MIN:
        tg.edit_message_text(dm_chat_id, message_id, debt_limit_text())
        storage.clear_state(giver_id)
        return
    buttons = [
        {"text": f"{n} {KABANKOIN_EMOJI}", "callback_data": f"cbet:{n}"}
        for n in range(CASINO_COINS_BET_MIN, max_bet + 1)
    ]
    tg.edit_message_text(
        dm_chat_id,
        message_id,
        f"{KABANKOIN_EMOJI} Баланс: {balance}\nВыберите ставку — больше ставка, выше шанс на крупный куш:",
        reply_markup={"inline_keyboard": [buttons, [CANCEL_BUTTON]]},
    )
    storage.set_state(
        giver_id,
        {"step": "casino_bet", "chat_id": str(chat_id), "reason": reason, "message_id": message_id},
    )


def resolve_coin_spin(giver, dm_chat_id, message_id, chat_id, bet, reason) -> None:
    storage.clear_state(giver["id"])
    giver_name = display_name(giver)

    spent, before, after = storage.spend_kabankoins_on_credit(
        chat_id, giver["id"], current_day_key(), bet, KABANKOIN_DAILY_AMOUNT, KABANKOIN_DEBT_BAN_LEVEL
    )
    if not spent:
        tg.edit_message_text(dm_chat_id, message_id, debt_limit_text())
        return
    if after < 0:
        apply_debt_penalty(giver, chat_id, before, after)

    balance = after
    payout = roll_kabankoin_payout(bet)
    if payout > 0:
        balance = storage.add_kabankoins(
            chat_id, giver["id"], current_day_key(), payout, KABANKOIN_DAILY_AMOUNT
        )

    jackpot = payout == 100
    if payout == 0:
        outcome = "ничего не выпало 😢"
    elif jackpot:
        outcome = f"выпадает {payout} 🎉 ДЖЕКПОТ!"
    else:
        outcome = f"выпадает {payout}"

    tg.edit_message_text(
        dm_chat_id, message_id, f"{KABANKOIN_EMOJI} {'Пусто' if payout == 0 else f'Выпало: {payout}'}"
    )

    details = f"\nПричина: {reason}" if reason else ""
    tg.send_message(
        chat_id,
        f"{KABANKOIN_EMOJI} {giver_name} крутит кабанкоин-казино, ставка {bet} — "
        f"{outcome}\nБаланс: {balance} {KABANKOIN_EMOJI}{details}",
    )


# --- /pay: send kabankoins to another participant --------------------------


def start_pay_flow(giver: dict, dm_chat_id, amount: int | None) -> None:
    """Entry point for /pay from the bot's DM: pick a chat first if there are several."""
    chats = storage.list_user_chats(giver["id"])
    if not chats:
        tg.send_message(
            dm_chat_id,
            "Я пока не знаю ни одного чата, где вы состоите.\n"
            f"Напишите что-нибудь в группе, где я работаю, и повторите {PAY_COMMAND}.",
        )
        return

    if len(chats) == 1:
        show_pay_target_picker(giver, dm_chat_id, chats[0][0], None, amount)
        return

    keyboard = [[{"text": title, "callback_data": f"paychat:{cid}"}] for cid, title in chats]
    keyboard.append([CANCEL_BUTTON])
    sent = tg.send_message(
        dm_chat_id, "В каком чате отправить кабанкоины?", reply_markup={"inline_keyboard": keyboard}
    )
    storage.set_state(
        giver["id"], {"step": "pay_chat", "amount": amount, "message_id": sent.get("message_id")}
    )


def show_pay_target_picker(giver: dict, dm_chat_id, chat_id, message_id, amount: int | None) -> None:
    sync_administrators(chat_id)
    participants = [
        (uid, name) for uid, name in storage.list_participants(chat_id) if str(uid) != str(giver["id"])
    ]

    if not participants:
        text = (
            "В этом чате я пока никого не видел.\n"
            "Участники появятся в списке после того, как напишут что-нибудь в чате."
        )
        keyboard = None
    else:
        text = f"Кому отправить {amount} {KABANKOIN_EMOJI}?" if amount else "Кому отправить кабанкоины?"
        keyboard = {
            "inline_keyboard": [
                *[
                    [{"text": name, "callback_data": f"payuser:{chat_id}:{uid}"}]
                    for uid, name in participants
                ],
                [CANCEL_BUTTON],
            ]
        }

    if message_id is None:
        sent = send_to_dm(giver, chat_id, text, keyboard)
        if sent is None:
            return
        message_id = sent.get("message_id")
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)

    storage.set_state(
        giver["id"],
        {"step": "pay_target", "chat_id": str(chat_id), "amount": amount, "message_id": message_id},
    )


def ask_pay_amount(giver_id, dm_chat_id, message_id, chat_id, target_id) -> None:
    storage.set_state(
        giver_id,
        {
            "step": "pay_amount",
            "chat_id": str(chat_id),
            "target_id": str(target_id),
            "message_id": message_id,
        },
    )
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    balance = get_kabankoins(chat_id, giver_id)
    tg.edit_message_text(
        dm_chat_id,
        message_id,
        f"{KABANKOIN_EMOJI} Кому: {target_name}\nВаш баланс: {balance}\n\n"
        "Отправьте сообщением, сколько кабанкоинов отправить.",
        reply_markup={"inline_keyboard": [[CANCEL_BUTTON]]},
    )


def finish_pay(giver: dict, ack_chat_id, chat_id, target_id, amount: int) -> None:
    storage.clear_state(giver["id"])
    giver_name = display_name(giver)
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    given_from_group = str(ack_chat_id) == str(chat_id)

    if str(target_id) == str(giver["id"]):
        tg.send_message(ack_chat_id, "Нельзя отправить кабанкоины самому себе.")
        return

    if not tg.is_chat_member(chat_id, giver["id"]):
        tg.send_message(ack_chat_id, "Вы больше не состоите в этом чате — перевод не выполнен.")
        return

    spent, giver_balance = storage.spend_kabankoins(
        chat_id, giver["id"], current_day_key(), amount, KABANKOIN_DAILY_AMOUNT
    )
    if not spent:
        tg.send_message(ack_chat_id, insufficient_balance_text(giver_balance))
        return

    target_balance = storage.add_kabankoins(
        chat_id, target_id, current_day_key(), amount, KABANKOIN_DAILY_AMOUNT
    )

    tg.send_message(
        chat_id,
        f"{KABANKOIN_EMOJI} {giver_name} отправляет {amount} {KABANKOIN_EMOJI} — {target_name}.\n"
        f"Баланс {giver_name}: {giver_balance} {KABANKOIN_EMOJI}\n"
        f"Баланс {target_name}: {target_balance} {KABANKOIN_EMOJI}",
    )
    if not given_from_group:
        tg.send_message(ack_chat_id, f"Готово, отправлено {amount} {KABANKOIN_EMOJI}: {target_name}.")


def _parse_amount(args: str) -> int | None:
    text = args.strip().split()[0] if args.strip() else ""
    if not text.isdigit():
        return None
    amount = int(text)
    return amount if amount > 0 else None


# --- /rename: buy a temporary admin badge for another participant ----------


def start_rename_flow(giver: dict, dm_chat_id) -> None:
    """Entry point for /rename from the bot's DM: pick a chat first if there are several."""
    chats = storage.list_user_chats(giver["id"])
    if not chats:
        tg.send_message(
            dm_chat_id,
            "Я пока не знаю ни одного чата, где вы состоите.\n"
            f"Напишите что-нибудь в группе, где я работаю, и повторите {RENAME_COMMAND}.",
        )
        return

    if len(chats) == 1:
        show_rename_target_picker(giver, dm_chat_id, chats[0][0], None)
        return

    keyboard = [[{"text": title, "callback_data": f"rnchat:{cid}"}] for cid, title in chats]
    keyboard.append([CANCEL_BUTTON])
    sent = tg.send_message(
        dm_chat_id, "В каком чате покупаем тег?", reply_markup={"inline_keyboard": keyboard}
    )
    storage.set_state(giver["id"], {"step": "rn_chat", "message_id": sent.get("message_id")})


def show_rename_target_picker(giver: dict, dm_chat_id, chat_id, message_id) -> None:
    sync_administrators(chat_id)
    participants = [
        (uid, name) for uid, name in storage.list_participants(chat_id) if str(uid) != str(giver["id"])
    ]

    if not participants:
        text = (
            "В этом чате я пока никого не видел.\n"
            "Участники появятся в списке после того, как напишут что-нибудь в чате."
        )
        keyboard = None
    else:
        text = "Кому купить тег?"
        keyboard = {
            "inline_keyboard": [
                *[
                    [{"text": name, "callback_data": f"rnuser:{chat_id}:{uid}"}]
                    for uid, name in participants
                ],
                [CANCEL_BUTTON],
            ]
        }

    if message_id is None:
        sent = send_to_dm(giver, chat_id, text, keyboard)
        if sent is None:
            return
        message_id = sent.get("message_id")
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)

    storage.set_state(giver["id"], {"step": "rn_target", "chat_id": str(chat_id), "message_id": message_id})


def show_rename_tier_picker(giver: dict, dm_chat_id, chat_id, message_id, target_id) -> None:
    balance = get_kabankoins(chat_id, giver["id"])
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    affordable = [(seconds, price) for seconds, price in NICKNAME_PRICE_TIERS if price <= balance]

    if not affordable:
        text = insufficient_balance_text(balance)
        if message_id is None:
            send_to_dm(giver, chat_id, text)
        else:
            tg.edit_message_text(dm_chat_id, message_id, text)
        storage.clear_state(giver["id"])
        return

    text = f"{KABANKOIN_EMOJI} Баланс: {balance}\nКому: {target_name}\nНа сколько купить тег?"
    keyboard = {
        "inline_keyboard": [
            *[
                [
                    {
                        "text": f"{price} {KABANKOIN_EMOJI} — {format_duration(seconds)}",
                        "callback_data": f"rntier:{seconds}:{price}",
                    }
                ]
                for seconds, price in affordable
            ],
            [CANCEL_BUTTON],
        ]
    }

    if message_id is None:
        sent = send_to_dm(giver, chat_id, text, keyboard)
        if sent is None:
            return
        message_id = sent.get("message_id")
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)

    storage.set_state(
        giver["id"],
        {"step": "rn_tier", "chat_id": str(chat_id), "target_id": str(target_id), "message_id": message_id},
    )


def ask_rename_title(giver_id, dm_chat_id, message_id, chat_id, target_id, seconds: int, price: int) -> None:
    storage.set_state(
        giver_id,
        {
            "step": "rn_title",
            "chat_id": str(chat_id),
            "target_id": str(target_id),
            "seconds": seconds,
            "price": price,
            "message_id": message_id,
        },
    )
    tg.edit_message_text(
        dm_chat_id,
        message_id,
        f"Отправьте текст тега сообщением — до {NICKNAME_MAX_LENGTH} символов.",
        reply_markup={"inline_keyboard": [[CANCEL_BUTTON]]},
    )


def finish_rename(
    giver: dict, ack_chat_id, chat_id, target_id, seconds: int, price: int, title_text: str
) -> None:
    storage.clear_state(giver["id"])
    giver_name = display_name(giver)
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    given_from_group = str(ack_chat_id) == str(chat_id)

    if not tg.is_chat_member(chat_id, giver["id"]):
        tg.send_message(ack_chat_id, "Вы больше не состоите в этом чате — покупка не выполнена.")
        return

    spent, balance = storage.spend_kabankoins(
        chat_id, giver["id"], current_day_key(), price, KABANKOIN_DAILY_AMOUNT
    )
    if not spent:
        tg.send_message(ack_chat_id, insufficient_balance_text(balance))
        return

    try:
        tg.promote_chat_member(chat_id, target_id)
        tg.set_chat_administrator_custom_title(chat_id, target_id, title_text)
    except Exception:
        # The badge didn't go through (usually a missing bot permission) —
        # don't charge for something that wasn't delivered.
        balance = storage.add_kabankoins(chat_id, giver["id"], current_day_key(), price, KABANKOIN_DAILY_AMOUNT)
        tg.send_message(
            ack_chat_id,
            "Не удалось выдать тег — боту не хватает прав. Дайте боту право "
            "«Назначение новых администраторов» в настройках чата. Кабанкоины возвращены.",
        )
        return

    storage.set_temp_title(chat_id, target_id, int(time.time()) + seconds)

    tg.send_message(
        chat_id,
        f"🏷 {giver_name} покупает {target_name} тег «{title_text}» на {format_duration(seconds)}.\n"
        f"Баланс {giver_name}: {balance} {KABANKOIN_EMOJI}",
    )
    if not given_from_group:
        tg.send_message(
            ack_chat_id, f"Готово, тег «{title_text}» выдан: {target_name} на {format_duration(seconds)}."
        )


# --- commands ---------------------------------------------------------------


def handle_yellow(message: dict, args: str, kind: str = "yellow") -> None:
    chat = message["chat"]

    if chat.get("type") not in GROUP_TYPES:
        start_give_flow(message["from"]["id"], chat["id"], kind)
        return

    giver = message["from"]
    reason = args.strip() or None
    reply = message.get("reply_to_message")

    if reply and "from" in reply:
        target = reply["from"]
        if target.get("is_bot"):
            tg.send_message(chat["id"], "Ботам карточки не выдаются.", message["message_id"])
            return
        if target["id"] == giver["id"]:
            tg.send_message(chat["id"], "Нельзя выдать карточку самому себе.", message["message_id"])
            return

        storage.remember_participant(chat["id"], target["id"], display_name(target))
        finish_give(giver, chat["id"], chat["id"], target["id"], reason, kind)
        return

    # No reply to point at someone: offer the picker in the chat itself.
    show_group_picker(chat["id"], giver, reason, kind)


def handle_green(message: dict, args: str) -> None:
    handle_yellow(message, args, kind="green")


def handle_casino(message: dict, args: str) -> None:
    chat = message["chat"]
    giver = message["from"]

    # Throttles the command itself, not any one chat's balance, so use the
    # caller's own id as both halves of the key.
    key = cooldown_key(giver["id"], giver["id"], "casino")
    remaining = kv.ttl(key)
    if remaining > 0:
        tg.send_message(
            chat["id"],
            f"Подождите ещё {format_duration(remaining)} перед следующей "
            f"{COOLDOWN_SUBJECT['casino']}.",
            message["message_id"],
        )
        return
    kv.set(key, "1", ex=COOLDOWN_FOR_KIND["casino"])

    if chat.get("type") not in GROUP_TYPES:
        start_casino_flow(giver, chat["id"])
        return

    reason = args.strip() or None
    reply = message.get("reply_to_message")
    target_id = None
    if reply and "from" in reply:
        target = reply["from"]
        if target.get("is_bot"):
            tg.send_message(chat["id"], "Ботам карточки не выдаются.", message["message_id"])
            return
        if target["id"] != giver["id"]:
            target_id = target["id"]
            storage.remember_participant(chat["id"], target_id, display_name(target))

    open_casino_type_picker(giver, chat["id"], chat["id"], None, target_id, reason)


def handle_cards(message: dict, args: str) -> None:
    chat = message["chat"]

    if chat.get("type") in GROUP_TYPES:
        send_cards_list(chat["id"], chat["id"], None)
        return

    chats = storage.list_user_chats(message["from"]["id"])
    if not chats:
        tg.send_message(chat["id"], "Я пока не знаю ни одного чата, где вы состоите.")
        return
    if len(chats) == 1:
        send_cards_list(chats[0][0], chat["id"], None)
        return

    keyboard = [[{"text": title, "callback_data": f"list:{chat_id}"}] for chat_id, title in chats]
    tg.send_message(
        chat["id"],
        "Карточки какого чата показать?",
        reply_markup={"inline_keyboard": keyboard},
    )


def send_cards_list(source_chat_id, target_chat_id, message_id) -> None:
    rows = storage.list_cards(source_chat_id)
    if not rows:
        text = "Пока никто не получал карточек."
    else:
        entries = [
            f"{name}:\n🟨 {yellow} 🟥 {red}" + (f" 🟩 {green}" if green else "")
            for name, yellow, red, green in rows
        ]
        text = "Карточки участников чата:\n\n" + "\n\n".join(entries)

    if message_id is None:
        tg.send_message(target_chat_id, text)
    else:
        tg.edit_message_text(target_chat_id, message_id, text)


def handle_pay(message: dict, args: str) -> None:
    chat = message["chat"]
    giver = message["from"]

    if chat.get("type") not in GROUP_TYPES:
        start_pay_flow(giver, chat["id"], _parse_amount(args))
        return

    amount = _parse_amount(args)
    if amount is None:
        tg.send_message(
            chat["id"],
            f"Укажите сумму: {PAY_COMMAND} <количество>, например {PAY_COMMAND} 20.",
            message["message_id"],
        )
        return

    reply = message.get("reply_to_message")
    if reply and "from" in reply:
        target = reply["from"]
        if target.get("is_bot"):
            tg.send_message(chat["id"], "Ботам кабанкоины не отправляются.", message["message_id"])
            return
        if target["id"] == giver["id"]:
            tg.send_message(chat["id"], "Нельзя отправить кабанкоины самому себе.", message["message_id"])
            return
        storage.remember_participant(chat["id"], target["id"], display_name(target))
        finish_pay(giver, chat["id"], chat["id"], target["id"], amount)
        return

    show_pay_target_picker(giver, chat["id"], chat["id"], None, amount)


def handle_rename(message: dict, args: str) -> None:
    chat = message["chat"]
    giver = message["from"]

    if chat.get("type") not in GROUP_TYPES:
        start_rename_flow(giver, chat["id"])
        return

    reply = message.get("reply_to_message")
    if reply and "from" in reply:
        target = reply["from"]
        if target.get("is_bot"):
            tg.send_message(chat["id"], "Тег ботам не покупается.", message["message_id"])
            return
        if target["id"] == giver["id"]:
            tg.send_message(chat["id"], "Нельзя купить тег самому себе.", message["message_id"])
            return
        storage.remember_participant(chat["id"], target["id"], display_name(target))
        show_rename_tier_picker(giver, chat["id"], chat["id"], None, target["id"])
        return

    show_rename_target_picker(giver, chat["id"], chat["id"], None)


def handle_reset_coins(message: dict, args: str) -> None:
    """Force every known participant's kabankoin balance back to the daily default."""
    chat = message["chat"]
    if chat.get("type") not in GROUP_TYPES:
        tg.send_message(chat["id"], "Эта команда работает в групповом чате.")
        return

    giver = message["from"]
    status = tg.get_chat_member_status(chat["id"], giver["id"])
    if status not in ("creator", "administrator"):
        tg.send_message(
            chat["id"],
            "Сбросить балансы кабанкоинов может только администратор чата.",
            message["message_id"],
        )
        return

    day_key = current_day_key()
    participants = storage.list_participants(chat["id"])
    for uid, _ in participants:
        storage.reset_kabankoins(chat["id"], uid, day_key)

    tg.send_message(
        chat["id"],
        f"{KABANKOIN_EMOJI} Баланс сброшен до {KABANKOIN_DAILY_AMOUNT} у {len(participants)} "
        "участников.",
    )


def handle_start(message: dict, args: str) -> None:
    tg.send_message(
        message["chat"]["id"],
        "Бот жёлтых/красных карточек.\n\n"
        f"{GIVE_COMMAND} — выдать жёлтую карточку. Способы:\n"
        f"• в чате ответом на сообщение участника: {GIVE_COMMAND} [причина];\n"
        f"• в чате без ответа: {GIVE_COMMAND} [причина] — бот покажет список участников;\n"
        f"• в личке с ботом: {GIVE_COMMAND} — выбрать чат, участника и причину.\n"
        f"Выдавать можно не чаще раза в {format_duration(GIVE_COOLDOWN_SECONDS)}.\n\n"
        f"После {YELLOW_THRESHOLD}-й жёлтой карточки участник получает красную "
        "и мутится в чате. Каждая следующая красная карточка за день мутит "
        "дольше предыдущей: "
        + ", ".join(format_duration(s) for s in MUTE_LADDER_SECONDS)
        + ". Каждый день отсчёт начинается заново.\n\n"
        f"{GREEN_COMMAND} — выдать зелёную карточку теми же тремя способами. "
        "Она снимает одну жёлтую, а если снимать нечего — копится про запас "
        "и гасит будущие жёлтые, по одной за раз. Больше "
        f"{GREEN_IMMUNITY_LIMIT} неиспользованных не накопить. Выдавать можно "
        f"не чаще раза в {format_duration(GREEN_COOLDOWN_SECONDS)}.\n\n"
        f"{KABANKOIN_EMOJI} У каждого свои кабанкоины — {KABANKOIN_DAILY_AMOUNT} в день "
        "на чат, обновляются каждую полночь. Тратятся на прокрутки казино.\n\n"
        f"{CASINO_COMMAND} — казино, два типа на выбор:\n"
        f"• 🎴 Карточное (1 {KABANKOIN_EMOJI} за прокрутку) — выберите участника, "
        f"затем соберите комбинацию из {CASINO_SLOTS} символов, взятых на эту "
        f"прокрутку из набора {' '.join(CASINO_SYMBOLS)}. Бот заранее прячет "
        f"{CASINO_LOSING_COMBOS} проигрышные комбинации: попали — осечка, красная "
        "карточка достаётся вам; мимо — участник получает карточку случайного "
        "цвета, красная выпадает реже остальных.\n"
        f"• {KABANKOIN_EMOJI} Кабанкоин — ставка от {CASINO_COINS_BET_MIN} до "
        f"{CASINO_COINS_BET_MAX} {KABANKOIN_EMOJI}, выигрыш от 1 до 100 (100 — "
        "джекпот). Чем больше ставка, тем выше шанс на крупный выигрыш.\n\n"
        f"Команду можно вызвать не чаще раза в "
        f"{format_duration(CASINO_COOLDOWN_SECONDS)}. Меню в обоих случаях "
        "открывается в личке с ботом.\n\n"
        "Ставки принимаются и без денег на балансе — казино даёт в долг, "
        f"до {KABANKOIN_DEBT_BAN_LEVEL} {KABANKOIN_EMOJI}. Долг "
        f"{KABANKOIN_DEBT_YELLOW_LEVEL} — жёлтая карточка, {KABANKOIN_DEBT_RED_LEVEL} — "
        f"красная, {KABANKOIN_DEBT_BAN_LEVEL} — бан на "
        f"{format_duration(KABANKOIN_DEBT_BAN_SECONDS)} и больше играть нельзя, "
        "пока баланс не подрастёт.\n\n"
        f"{PAY_COMMAND} <количество> — отправить кабанкоины другому участнику: "
        "ответом на сообщение, или без ответа — бот покажет список в личке.\n\n"
        f"{RENAME_COMMAND} — купить участнику тег администратора (виден рядом с "
        "именем в чате, сам ник и @username в Telegram не меняются): "
        + ", ".join(f"{price} {KABANKOIN_EMOJI} на {format_duration(s)}" for s, price in NICKNAME_PRICE_TIERS)
        + f". Тег до {NICKNAME_MAX_LENGTH} символов, снимается ботом при первом "
        "сообщении в чате после истечения срока.\n\n"
        f"{LIST_COMMAND} — список карточек участников чата.",
    )


GIVE_COMMAND = "/card"
GREEN_COMMAND = "/green"
CASINO_COMMAND = "/casino"
LIST_COMMAND = "/list"
PAY_COMMAND = "/pay"
RENAME_COMMAND = "/rename"

COMMAND_FOR_KIND = {
    "yellow": GIVE_COMMAND,
    "green": GREEN_COMMAND,
    "casino": CASINO_COMMAND,
}

COMMANDS = {
    GIVE_COMMAND: handle_yellow,
    GREEN_COMMAND: handle_green,
    CASINO_COMMAND: handle_casino,
    LIST_COMMAND: handle_cards,
    PAY_COMMAND: handle_pay,
    RENAME_COMMAND: handle_rename,
    "/resetcoins": handle_reset_coins,
    # Previous names, kept working as aliases.
    "/yellow": handle_yellow,
    "/cards": handle_cards,
    "/kabany": handle_cards,
    "/start": handle_start,
    "/help": handle_start,
}


# --- update routing ---------------------------------------------------------


def _handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    user = callback["from"]
    message = callback["message"]
    dm_chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    state = storage.get_state(user["id"])

    # A picker posted in a group is visible to everyone, so only the person who
    # asked for it — the one holding the matching state — may use its buttons.
    needs_state = data.startswith(
        (
            "gu:", "sl:", "cchat:", "ctype:", "cuser:", "cbet:",
            "paychat:", "payuser:", "rnchat:", "rnuser:", "rntier:",
        )
    ) or (data == "cancel" and message["chat"].get("type") in GROUP_TYPES)
    if needs_state and not state:
        if data.startswith(("sl:", "cbet:")):
            text = "Это не ваша прокрутка."
        elif data.startswith(("paychat:", "payuser:")):
            text = "Это не ваш перевод."
        elif data.startswith(("rnchat:", "rnuser:", "rntier:")):
            text = "Это не ваш тег."
        else:
            text = "Это не ваша карточка."
        tg.answer_callback_query(callback["id"], text)
        return

    tg.answer_callback_query(callback["id"])

    if data == "cancel":
        storage.clear_state(user["id"])
        tg.edit_message_text(dm_chat_id, message_id, "Отменено.")
        return

    if data.startswith("sl:"):
        # Only the machine this state was created for may be resolved by it.
        if state.get("step") != "slots" or state.get("message_id") != message_id:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Прокрутка устарела, начните заново: {CASINO_COMMAND}"
            )
            return
        pick_slot_symbol(user, state, dm_chat_id, message_id, int(data.split(":", 1)[1]))
        return

    if data.startswith("gu:"):
        # Same guard as the slot machine: an older picker must not act on the
        # state a newer one created.
        if state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, "Этот список устарел, вызовите команду заново.")
            return

        _, kind, chat_id, target_id = data.split(":", 3)
        target_name = storage.get_name(chat_id, target_id) or str(target_id)
        tg.edit_message_text(dm_chat_id, message_id, f"{CARD_EMOJI[kind]} Выбран: {target_name}")
        finish_give(user, chat_id, chat_id, target_id, state.get("reason"), kind)
        return

    if data.startswith("chat:"):
        _, kind, chat_id = data.split(":", 2)
        ask_for_target(dm_chat_id, message_id, chat_id, user["id"], kind)
        return

    if data.startswith("list:"):
        chat_id = data.split(":", 1)[1]
        send_cards_list(chat_id, dm_chat_id, message_id)
        return

    if data.startswith("user:"):
        _, kind, chat_id, target_id = data.split(":", 3)
        ask_for_reason(dm_chat_id, message_id, chat_id, target_id, user["id"], kind)
        return

    if data.startswith("cchat:"):
        if state.get("step") != "casino_chat" or state.get("message_id") != message_id:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Устарело, начните заново: {CASINO_COMMAND}"
            )
            return
        chat_id = data.split(":", 1)[1]
        open_casino_type_picker(user, dm_chat_id, chat_id, message_id, None, state.get("reason"))
        return

    if data.startswith("ctype:"):
        if state.get("step") != "casino_type" or state.get("message_id") != message_id:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Устарело, начните заново: {CASINO_COMMAND}"
            )
            return
        casino_type = data.split(":", 1)[1]
        chat_id = state["chat_id"]
        reason = state.get("reason")
        if casino_type == "cards":
            target_id = state.get("target_id")
            if target_id:
                begin_casino_cards(user, chat_id, target_id, reason, dm_chat_id, message_id)
            else:
                show_casino_target_picker(user["id"], dm_chat_id, message_id, chat_id, reason)
        else:
            show_bet_picker(user["id"], dm_chat_id, message_id, chat_id, reason)
        return

    if data.startswith("cuser:"):
        if state.get("step") != "casino_pick" or state.get("message_id") != message_id:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Устарело, начните заново: {CASINO_COMMAND}"
            )
            return
        target_id = data.split(":", 1)[1]
        begin_casino_cards(user, state["chat_id"], target_id, state.get("reason"), dm_chat_id, message_id)
        return

    if data.startswith("cbet:"):
        if state.get("step") != "casino_bet" or state.get("message_id") != message_id:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Устарело, начните заново: {CASINO_COMMAND}"
            )
            return
        bet = int(data.split(":", 1)[1])
        resolve_coin_spin(user, dm_chat_id, message_id, state["chat_id"], bet, state.get("reason"))
        return

    if data.startswith("paychat:"):
        if state.get("step") != "pay_chat" or state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, f"Устарело, начните заново: {PAY_COMMAND}")
            return
        chat_id = data.split(":", 1)[1]
        show_pay_target_picker(user, dm_chat_id, chat_id, message_id, state.get("amount"))
        return

    if data.startswith("payuser:"):
        if state.get("step") != "pay_target" or state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, f"Устарело, начните заново: {PAY_COMMAND}")
            return
        _, chat_id, target_id = data.split(":", 2)
        amount = state.get("amount")
        if amount:
            target_name = storage.get_name(chat_id, target_id) or str(target_id)
            tg.edit_message_text(dm_chat_id, message_id, f"{KABANKOIN_EMOJI} Выбран: {target_name}")
            finish_pay(user, dm_chat_id, chat_id, target_id, amount)
        else:
            ask_pay_amount(user["id"], dm_chat_id, message_id, chat_id, target_id)
        return

    if data.startswith("rnchat:"):
        if state.get("step") != "rn_chat" or state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, f"Устарело, начните заново: {RENAME_COMMAND}")
            return
        chat_id = data.split(":", 1)[1]
        show_rename_target_picker(user, dm_chat_id, chat_id, message_id)
        return

    if data.startswith("rnuser:"):
        if state.get("step") != "rn_target" or state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, f"Устарело, начните заново: {RENAME_COMMAND}")
            return
        _, chat_id, target_id = data.split(":", 2)
        show_rename_tier_picker(user, dm_chat_id, chat_id, message_id, target_id)
        return

    if data.startswith("rntier:"):
        if state.get("step") != "rn_tier" or state.get("message_id") != message_id:
            tg.edit_message_text(dm_chat_id, message_id, f"Устарело, начните заново: {RENAME_COMMAND}")
            return
        _, seconds, price = data.split(":", 2)
        ask_rename_title(
            user["id"], dm_chat_id, message_id, state["chat_id"], state["target_id"], int(seconds), int(price)
        )
        return

    if data == "noreason":
        if not state:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Выдача карточки устарела, начните заново: {GIVE_COMMAND}"
            )
            return
        tg.edit_message_text(dm_chat_id, message_id, "Карточка выдаётся без указания причины.")
        finish_give(
            user, dm_chat_id, state["chat_id"], state["target_id"], None, state.get("kind", "yellow")
        )


def _handle_private_text(message: dict) -> None:
    """A plain message in the bot's DM answers whichever step is pending."""
    user = message["from"]
    state = storage.get_state(user["id"])
    if not state:
        return
    step = state.get("step")

    if step == "reason":
        finish_give(
            user,
            message["chat"]["id"],
            state["chat_id"],
            state["target_id"],
            message["text"].strip(),
            state.get("kind", "yellow"),
        )
        return

    if step == "pay_amount":
        text = message["text"].strip()
        if not text.isdigit() or int(text) <= 0:
            tg.send_message(message["chat"]["id"], "Нужно целое положительное число. Отправьте сумму ещё раз.")
            return
        finish_pay(user, message["chat"]["id"], state["chat_id"], state["target_id"], int(text))
        return

    if step == "rn_title":
        text = message["text"].strip()
        if not text or len(text) > NICKNAME_MAX_LENGTH:
            tg.send_message(
                message["chat"]["id"],
                f"Тег должен быть от 1 до {NICKNAME_MAX_LENGTH} символов. Отправьте другой текст.",
            )
            return
        finish_rename(
            user,
            message["chat"]["id"],
            state["chat_id"],
            state["target_id"],
            state["seconds"],
            state["price"],
            text,
        )
        return


def _dispatch(update: dict) -> None:
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
        return

    for key in ("chat_member", "my_chat_member"):
        if key in update:
            _remember_membership(update[key])
            return

    message = update.get("message")
    if not message:
        return

    _remember(message)

    if "text" not in message:
        return

    text = message["text"]
    if not text.startswith("/"):
        if message["chat"].get("type") == "private":
            _handle_private_text(message)
        return

    first_word, _, rest = text.partition(" ")
    command = first_word.split("@")[0]

    handler = COMMANDS.get(command)
    if handler:
        handler(message, rest)


def _check_kv() -> str:
    if "KV_REST_API_URL" not in os.environ or "KV_REST_API_TOKEN" not in os.environ:
        candidates = sorted(
            key
            for key in os.environ
            if any(fragment in key.upper() for fragment in ("REDIS", "KV", "UPSTASH"))
        )
        return (
            "not configured: KV_REST_API_URL / KV_REST_API_TOKEN env vars are missing "
            f"(similarly named env vars found: {candidates or 'none'})"
        )
    try:
        kv.ttl("healthcheck")
    except Exception as exc:  # noqa: BLE001 - surface any KV error to the diagnostic endpoint
        return f"error: {exc}"
    return "ok"


def _health_response():
    return jsonify(
        {
            "ok": True,
            "service": "telegram-cards-bot",
            "bot_token_set": "BOT_TOKEN" in os.environ,
            "kv": _check_kv(),
        }
    )


@app.route("/", methods=["GET", "POST"])
@app.route("/api/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return _health_response()

    secret = os.environ.get("WEBHOOK_SECRET")
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return jsonify({"ok": False}), 401

    update = request.get_json(force=True, silent=True) or {}
    try:
        _dispatch(update)
    except Exception:
        # Always acknowledge the update: a non-200 makes Telegram redeliver the
        # same update indefinitely, turning one broken command into a retry loop.
        app.logger.exception("Failed to handle update")
    return jsonify({"ok": True})
