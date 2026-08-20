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
    MUTE_LADDER_SECONDS,
    YELLOW_THRESHOLD,
)

app = Flask(__name__)

GROUP_TYPES = ("group", "supergroup")

CANCEL_BUTTON = {"text": "Отмена", "callback_data": "cancel"}

CARD_NAME = {"yellow": "жёлтую", "green": "зелёную", "casino": "случайную"}
CARD_NAME_NOMINATIVE = {"yellow": "жёлтая", "green": "зелёная", "red": "красная"}
CARD_EMOJI = {"yellow": "🟨", "green": "🟩", "red": "🟥", "casino": "🎰"}

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


def send_menu(giver: dict, group_chat_id, text: str, keyboard=None):
    """Put a menu in front of one person only.

    Telegram has no per-user message inside a group, so menus always go to the
    caller's private chat; only results are posted to the group. If the bot
    cannot write to them, say so in the group instead.
    """
    try:
        return tg.send_message(giver["id"], text, reply_markup=keyboard)
    except Exception:
        username = bot_username()
        hint = f" — @{username}" if username else ""
        tg.send_message(
            group_chat_id,
            f"{display_name(giver)}, меню я присылаю в личные сообщения. "
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

    if kind == "casino":
        start_slots(giver, chat_id, target_id, target_name, reason)
        return

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
        # Only the card that was thrown gets a colour; the green being spent
        # would otherwise compete with it for attention.
        marker = "" if header else "🟨 "
        tg.send_message(
            chat_id,
            f"{header}{marker}{target_name} тратит зелёную карточку — жёлтая не засчитана."
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


def slots_text(giver_name: str, target_name: str, symbols: list[str], picked: list[str]) -> str:
    reels = " ".join(picked + ["⬜"] * (CASINO_SLOTS - len(picked)))
    return (
        f"🎰 {giver_name} крутит казино на {target_name}\n"
        f"Барабаны этой прокрутки: {' '.join(symbols)}\n\n"
        f"{reels}\n\n"
        f"Символ {len(picked) + 1} из {CASINO_SLOTS} — выбирайте."
    )


def start_slots(giver: dict, chat_id, target_id, target_name, reason) -> None:
    giver_name = display_name(giver)

    # Each spin runs on its own reels, drawn from the full symbol set.
    symbols = random.sample(CASINO_SYMBOLS, CASINO_REEL_SYMBOLS)
    every_combo = ["".join(c) for c in itertools.product(symbols, repeat=CASINO_SLOTS)]

    sent = send_menu(
        giver,
        chat_id,
        slots_text(giver_name, target_name, symbols, []),
        slots_keyboard(symbols),
    )
    if sent is None:
        return

    storage.set_state(
        giver["id"],
        {
            "step": "slots",
            "chat_id": str(chat_id),
            "target_id": str(target_id),
            "reason": reason,
            "symbols": symbols,
            "picked": [],
            # Drawn per spin and revealed afterwards, so the odds stay honest
            # even though the player picks the symbols by hand.
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
            slots_text(giver_name, target_name, symbols, picked),
            reply_markup=slots_keyboard(symbols),
        )
        return

    resolve_slots(giver, state, dm_chat_id, message_id, picked, chat_id, target_id, target_name)


def resolve_slots(
    giver, state, ack_chat_id, message_id, picked, chat_id, target_id, target_name
) -> None:
    storage.clear_state(giver["id"])

    giver_name = display_name(giver)
    combo = "".join(picked)
    losing = state["losing"]
    backfired = combo in losing

    tg.edit_message_text(
        ack_chat_id,
        message_id,
        f"🎰 {' '.join(picked)}\n"
        + ("Осечка — красная ваша." if backfired else "Мимо. Результат ушёл в чат."),
    )

    header = (
        f"🎰 {giver_name} крутит казино на {target_name}\n"
        f"Комбинация: {' '.join(picked)}\n"
        f"Проигрышные были: {' · '.join(losing)}\n"
    )

    if backfired:
        storage.remember_participant(chat_id, giver["id"], giver_name)
        header += "Осечка! 🟥 Красная карточка достаётся тому, кто крутил\n"
        apply_card(
            giver_name,
            chat_id,
            chat_id,
            giver["id"],
            giver_name,
            "red",
            _details(giver_name, state.get("reason"), "Крутил"),
            header,
            backfired=True,
        )
        return

    kind = random.choices(
        list(CASINO_OUTCOME_WEIGHTS), weights=list(CASINO_OUTCOME_WEIGHTS.values())
    )[0]
    header += f"Мимо — выпала {CARD_EMOJI[kind]} {CARD_NAME_NOMINATIVE[kind]} карточка\n"
    apply_card(
        giver_name,
        chat_id,
        chat_id,
        target_id,
        target_name,
        kind,
        _details(giver_name, state.get("reason"), "Крутил"),
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
    """Offer the chat's participants to the caller — privately, in their DM."""
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

    title = storage.list_user_chats(giver["id"])
    chat_title = next((t for c, t in title if str(c) == str(chat_id)), str(chat_id))

    prompt = f"Чат: {chat_title}\nКому выдать {CARD_NAME[kind]} карточку?"
    if reason:
        prompt += f"\nПричина: {reason}"

    sent = send_menu(
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
        {"step": "pick", "kind": kind, "chat_id": str(chat_id), "reason": reason},
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

    # No reply to point at someone: offer the same picker, right in the chat.
    show_group_picker(chat["id"], giver, reason, kind)


def handle_green(message: dict, args: str) -> None:
    handle_yellow(message, args, kind="green")


def handle_casino(message: dict, args: str) -> None:
    handle_yellow(message, args, kind="casino")


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
        f"{CASINO_COMMAND} — игровой автомат. Выберите участника; на каждую "
        f"прокрутку берутся {CASINO_REEL_SYMBOLS} случайных символа из набора "
        f"{' '.join(CASINO_SYMBOLS)}, из них вы собираете комбинацию длиной "
        f"{CASINO_SLOTS}. Бот заранее прячет {CASINO_LOSING_COMBOS} проигрышные "
        "комбинации: попали — осечка, красная карточка достаётся вам; мимо — "
        "участник получает карточку случайного цвета, красная выпадает реже "
        "остальных. Крутить можно раз в "
        f"{format_duration(CASINO_COOLDOWN_SECONDS)}.\n\n"
        "Все меню бот присылает в личку — в чате видно только результат.\n\n"
        f"{LIST_COMMAND} — список карточек участников чата.",
    )


GIVE_COMMAND = "/card"
GREEN_COMMAND = "/green"
CASINO_COMMAND = "/casino"
LIST_COMMAND = "/list"

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
    needs_state = data.startswith(("gu:", "sl:")) or (
        data == "cancel" and message["chat"].get("type") in GROUP_TYPES
    )
    if needs_state and not state:
        tg.answer_callback_query(
            callback["id"],
            "Это не ваша прокрутка." if data.startswith("sl:") else "Это не ваша карточка.",
        )
        return

    tg.answer_callback_query(callback["id"])

    if data == "cancel":
        storage.clear_state(user["id"])
        tg.edit_message_text(dm_chat_id, message_id, "Отменено.")
        return

    if data.startswith("sl:"):
        if state.get("step") != "slots":
            tg.edit_message_text(
                dm_chat_id, message_id, f"Прокрутка устарела, начните заново: {CASINO_COMMAND}"
            )
            return
        pick_slot_symbol(user, state, dm_chat_id, message_id, int(data.split(":", 1)[1]))
        return

    if data.startswith("gu:"):
        _, kind, chat_id, target_id = data.split(":", 3)
        target_name = storage.get_name(chat_id, target_id) or str(target_id)
        tg.edit_message_text(
            dm_chat_id,
            message_id,
            f"{display_name(user)} выдаёт {CARD_NAME[kind]} карточку: {target_name}",
        )
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
    """A plain message in the bot's DM is the reason for a pending card."""
    user = message["from"]
    state = storage.get_state(user["id"])
    if not state or state.get("step") != "reason":
        return

    finish_give(
        user,
        message["chat"]["id"],
        state["chat_id"],
        state["target_id"],
        message["text"].strip(),
        state.get("kind", "yellow"),
    )


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
