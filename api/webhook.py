import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request  # noqa: E402

import kv  # noqa: E402
import storage_kv as storage  # noqa: E402
import telegram_api as tg  # noqa: E402
from config import GIVE_COOLDOWN_SECONDS, MUTE_SECONDS, YELLOW_THRESHOLD  # noqa: E402

app = Flask(__name__)

GROUP_TYPES = ("group", "supergroup")

CANCEL_BUTTON = {"text": "Отмена", "callback_data": "cancel"}


def display_name(user: dict) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    return name or str(user["id"])


def cooldown_key(chat_id, giver_id) -> str:
    return f"cooldown:{chat_id}:{giver_id}"


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


# --- the card dialog, which runs in the bot's private chat -------------------


def start_give_flow(user_id, dm_chat_id) -> None:
    chats = storage.list_user_chats(user_id)

    if not chats:
        tg.send_message(
            dm_chat_id,
            "Я пока не знаю ни одного чата, где вы состоите.\n"
            f"Напишите что-нибудь в группе, где я работаю, и повторите {GIVE_COMMAND}.",
        )
        return

    if len(chats) == 1:
        ask_for_target(dm_chat_id, None, chats[0][0], user_id)
        return

    keyboard = [[{"text": title, "callback_data": f"chat:{chat_id}"}] for chat_id, title in chats]
    keyboard.append([CANCEL_BUTTON])
    tg.send_message(
        dm_chat_id,
        "В каком чате выдать карточку?",
        reply_markup={"inline_keyboard": keyboard},
    )


def ask_for_target(dm_chat_id, message_id, chat_id, giver_id=None) -> None:
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
        text = "Кому выдать жёлтую карточку?"
        keyboard = {
            "inline_keyboard": [
                *[[{"text": name, "callback_data": f"user:{chat_id}:{uid}"}] for uid, name in participants],
                [CANCEL_BUTTON],
            ]
        }

    if message_id is None:
        tg.send_message(dm_chat_id, text, reply_markup=keyboard)
    else:
        tg.edit_message_text(dm_chat_id, message_id, text, reply_markup=keyboard)


def ask_for_reason(dm_chat_id, message_id, chat_id, target_id, giver_id) -> None:
    storage.set_state(
        giver_id, {"step": "reason", "chat_id": str(chat_id), "target_id": str(target_id)}
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
        f"Кому: {target_name}\n\nОтправьте причину сообщением "
        "или нажмите «Без причины».",
        reply_markup=keyboard,
    )


def finish_give(giver: dict, ack_chat_id, chat_id, target_id, reason: str | None) -> None:
    """Award the card in `chat_id`; progress and errors go to `ack_chat_id`."""
    storage.clear_state(giver["id"])

    giver_name = display_name(giver)
    target_name = storage.get_name(chat_id, target_id) or str(target_id)
    given_from_group = str(ack_chat_id) == str(chat_id)

    if not tg.is_chat_member(chat_id, giver["id"]):
        tg.send_message(ack_chat_id, "Вы больше не состоите в этом чате — карточка не выдана.")
        return

    key = cooldown_key(chat_id, giver["id"])
    remaining = kv.ttl(key)
    if remaining > 0:
        tg.send_message(ack_chat_id, f"Подождите ещё {remaining} сек. перед следующей карточкой.")
        return
    kv.set(key, "1", ex=GIVE_COOLDOWN_SECONDS)

    yellow, red = storage.add_yellow_card(chat_id, target_id, target_name, YELLOW_THRESHOLD)

    details = f"\nВыдал: {giver_name}"
    if reason:
        details += f"\nПричина: {reason}"

    if yellow == 0 and red > 0:
        until = int(time.time()) + MUTE_SECONDS
        try:
            tg.restrict_chat_member(chat_id, target_id, until)
            tg.send_message(
                chat_id,
                f"🟥 {target_name} получает красную карточку (всего красных: {red}) "
                f"и заглушен в чате на {MUTE_SECONDS} секунд.{details}",
            )
        except Exception:
            tg.send_message(
                chat_id,
                f"🟥 {target_name} получает красную карточку (всего красных: {red}), "
                "но заглушить не удалось — дайте боту права администратора "
                "с включённым правом «Блокировка пользователей» "
                f"(и учтите: администратора чата бот заглушить не может).{details}",
            )
    else:
        tg.send_message(
            chat_id,
            f"🟨 {target_name} получает жёлтую карточку ({yellow}/{YELLOW_THRESHOLD}).{details}",
        )

    if not given_from_group:
        tg.send_message(ack_chat_id, f"Готово, карточка выдана: {target_name}.")


def show_group_picker(chat_id, giver: dict, reason: str | None) -> None:
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

    # The reason can be longer than callback_data allows, so it waits in the
    # state until the giver taps a name.
    storage.set_state(
        giver["id"], {"step": "pick", "chat_id": str(chat_id), "reason": reason}
    )

    prompt = f"{display_name(giver)}, кому выдать жёлтую карточку?"
    if reason:
        prompt += f"\nПричина: {reason}"

    tg.send_message(
        chat_id,
        prompt,
        reply_markup={
            "inline_keyboard": [
                *[[{"text": name, "callback_data": f"gu:{chat_id}:{uid}"}] for uid, name in participants],
                [CANCEL_BUTTON],
            ]
        },
    )


# --- commands ---------------------------------------------------------------


def handle_yellow(message: dict, args: str) -> None:
    chat = message["chat"]

    if chat.get("type") not in GROUP_TYPES:
        start_give_flow(message["from"]["id"], chat["id"])
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
        finish_give(giver, chat["id"], chat["id"], target["id"], reason)
        return

    # No reply to point at someone: offer the same picker, right in the chat.
    show_group_picker(chat["id"], giver, reason)


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
        lines = ["Карточки участников чата:"]
        for name, yellow, red in rows:
            lines.append(f"{name}: 🟨 {yellow} 🟥 {red}")
        text = "\n".join(lines)

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
        f"Выдавать карточки можно не чаще раза в {GIVE_COOLDOWN_SECONDS} секунд.\n"
        f"После {YELLOW_THRESHOLD}-й жёлтой карточки участник получает красную "
        f"и мутится в чате на {MUTE_SECONDS} секунд.\n\n"
        f"{LIST_COMMAND} — список карточек участников чата.",
    )


GIVE_COMMAND = "/card"
LIST_COMMAND = "/list"

COMMANDS = {
    GIVE_COMMAND: handle_yellow,
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
    if data.startswith("gu:") or (data == "cancel" and message["chat"].get("type") in GROUP_TYPES):
        if not state:
            tg.answer_callback_query(callback["id"], "Это не ваша карточка.")
            return

    tg.answer_callback_query(callback["id"])

    if data == "cancel":
        storage.clear_state(user["id"])
        tg.edit_message_text(dm_chat_id, message_id, "Отменено.")
        return

    if data.startswith("gu:"):
        _, chat_id, target_id = data.split(":", 2)
        target_name = storage.get_name(chat_id, target_id) or str(target_id)
        tg.edit_message_text(
            dm_chat_id, message_id, f"{display_name(user)} выдаёт карточку: {target_name}"
        )
        finish_give(user, chat_id, chat_id, target_id, state.get("reason"))
        return

    if data.startswith("chat:"):
        chat_id = data.split(":", 1)[1]
        ask_for_target(dm_chat_id, message_id, chat_id, user["id"])
        return

    if data.startswith("list:"):
        chat_id = data.split(":", 1)[1]
        send_cards_list(chat_id, dm_chat_id, message_id)
        return

    if data.startswith("user:"):
        _, chat_id, target_id = data.split(":", 2)
        ask_for_reason(dm_chat_id, message_id, chat_id, target_id, user["id"])
        return

    if data == "noreason":
        if not state:
            tg.edit_message_text(
                dm_chat_id, message_id, f"Выдача карточки устарела, начните заново: {GIVE_COMMAND}"
            )
            return
        tg.edit_message_text(dm_chat_id, message_id, "Карточка выдаётся без указания причины.")
        finish_give(user, dm_chat_id, state["chat_id"], state["target_id"], None)


def _handle_private_text(message: dict) -> None:
    """A plain message in the bot's DM is the reason for a pending card."""
    user = message["from"]
    state = storage.get_state(user["id"])
    if not state or state.get("step") != "reason":
        return

    finish_give(user, message["chat"]["id"], state["chat_id"], state["target_id"], message["text"].strip())


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
