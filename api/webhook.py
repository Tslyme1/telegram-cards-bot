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


def display_name(user: dict) -> str:
    username = user.get("username")
    if username:
        return f"@{username}"
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    return name or str(user["id"])


def cooldown_key(chat_id, giver_id) -> str:
    return f"cooldown:{chat_id}:{giver_id}"


def handle_yellow(message: dict, args: str) -> None:
    chat = message["chat"]
    chat_id = chat["id"]
    reply_to = message["message_id"]

    if chat.get("type") == "private":
        tg.send_message(chat_id, "Эта команда работает только в групповых чатах.", reply_to)
        return

    reply = message.get("reply_to_message")
    if not reply or "from" not in reply:
        tg.send_message(
            chat_id,
            "Ответьте командой /yellow на сообщение участника, которому хотите выдать карточку.",
            reply_to,
        )
        return

    giver = message["from"]
    target = reply["from"]

    if target.get("is_bot"):
        tg.send_message(chat_id, "Ботам карточки не выдаются.", reply_to)
        return

    if target["id"] == giver["id"]:
        tg.send_message(chat_id, "Нельзя выдать карточку самому себе.", reply_to)
        return

    key = cooldown_key(chat_id, giver["id"])
    remaining = kv.ttl(key)
    if remaining > 0:
        tg.send_message(chat_id, f"Подождите ещё {remaining} сек. перед следующей карточкой.", reply_to)
        return
    kv.set(key, "1", ex=GIVE_COOLDOWN_SECONDS)

    reason = args.strip() or None
    reason_suffix = f"\nПричина: {reason}" if reason else ""

    yellow, red = storage.add_yellow_card(chat_id, target["id"], display_name(target), YELLOW_THRESHOLD)

    if yellow == 0 and red > 0:
        until = int(time.time()) + MUTE_SECONDS
        try:
            tg.restrict_chat_member(chat_id, target["id"], until)
            tg.send_message(
                chat_id,
                f"🟥 {display_name(target)} получает красную карточку (всего красных: {red}) "
                f"и заглушен в чате на {MUTE_SECONDS} секунд.{reason_suffix}",
                reply_to,
            )
        except Exception:
            tg.send_message(
                chat_id,
                f"🟥 {display_name(target)} получает красную карточку (всего красных: {red}), "
                "но заглушить не удалось — дайте боту права администратора "
                f"с разрешением «Ограничивать участников» (и убедитесь, что цель не админ).{reason_suffix}",
                reply_to,
            )
    else:
        tg.send_message(
            chat_id,
            f"🟨 {display_name(target)} получает жёлтую карточку ({yellow}/{YELLOW_THRESHOLD}).{reason_suffix}",
            reply_to,
        )


def handle_cards(message: dict, args: str) -> None:
    chat = message["chat"]
    if chat.get("type") == "private":
        tg.send_message(chat["id"], "Эта команда работает только в групповых чатах.")
        return

    rows = storage.list_cards(chat["id"])
    if not rows:
        tg.send_message(chat["id"], "Пока никто не получал карточек.")
        return

    lines = ["Карточки участников чата:"]
    for name, yellow, red in rows:
        lines.append(f"{name}: 🟨 {yellow} 🟥 {red}")
    tg.send_message(chat["id"], "\n".join(lines))


def handle_start(message: dict, args: str) -> None:
    tg.send_message(
        message["chat"]["id"],
        "Бот жёлтых/красных карточек.\n\n"
        f"/yellow [причина] — ответом на сообщение участника выдать ему жёлтую карточку "
        f"(не чаще раза в {GIVE_COOLDOWN_SECONDS} секунд с одного аккаунта).\n"
        f"После {YELLOW_THRESHOLD}-й жёлтой карточки участник получает красную "
        f"и мутится в чате на {MUTE_SECONDS} секунд.\n"
        "/cards — список карточек участников чата.",
    )


COMMANDS = {
    "/yellow": handle_yellow,
    "/cards": handle_cards,
    "/start": handle_start,
    "/help": handle_start,
}


def _dispatch(update: dict) -> None:
    message = update.get("message")
    if not message or "text" not in message:
        return

    text = message["text"]
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
    _dispatch(update)
    return jsonify({"ok": True})
