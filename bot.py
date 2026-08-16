import logging
import os
import time
from datetime import timedelta

from telegram import ChatPermissions, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes

import storage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

YELLOW_THRESHOLD = 6
GIVE_COOLDOWN_SECONDS = 10
MUTE_SECONDS = 30

# In-memory per-giver cooldown: {(chat_id, giver_id): last_given_unix_ts}
_last_given: dict[tuple[int, int], float] = {}

MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Бот жёлтых/красных карточек.\n\n"
        f"/yellow — ответом на сообщение участника выдать ему жёлтую карточку "
        f"(не чаще раза в {GIVE_COOLDOWN_SECONDS} секунд с одного аккаунта).\n"
        f"После {YELLOW_THRESHOLD}-й жёлтой карточки участник получает красную "
        f"и мутится в чате на {MUTE_SECONDS} секунд.\n"
        "/cards — список карточек участников чата.\n\n"
        "Чтобы мут работал, добавьте бота в чат администратором с правом "
        "«Ограничивать участников»."
    )


async def give_yellow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        await message.reply_text("Эта команда работает только в групповых чатах.")
        return

    if message.reply_to_message is None:
        await message.reply_text(
            "Ответьте командой /yellow на сообщение участника, которому хотите выдать карточку."
        )
        return

    giver = update.effective_user
    target = message.reply_to_message.from_user

    if target.is_bot:
        await message.reply_text("Ботам карточки не выдаются.")
        return

    if target.id == giver.id:
        await message.reply_text("Нельзя выдать карточку самому себе.")
        return

    now = time.monotonic()
    key = (chat.id, giver.id)
    last = _last_given.get(key)
    if last is not None:
        elapsed = now - last
        if elapsed < GIVE_COOLDOWN_SECONDS:
            wait = GIVE_COOLDOWN_SECONDS - elapsed
            await message.reply_text(f"Подождите ещё {wait:.0f} сек. перед следующей карточкой.")
            return

    _last_given[key] = now

    yellow, red = storage.add_yellow_card(chat.id, target.id, display_name(target), YELLOW_THRESHOLD)

    if yellow == 0 and red > 0:
        # Threshold was just hit: yellow was reset and a red card was added.
        until = message.date + timedelta(seconds=MUTE_SECONDS)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=target.id,
                permissions=MUTE_PERMISSIONS,
                until_date=until,
            )
            await message.reply_text(
                f"🟥 {display_name(target)} получает красную карточку (всего красных: {red}) "
                f"и заглушен в чате на {MUTE_SECONDS} секунд."
            )
        except (BadRequest, Forbidden) as exc:
            logger.warning("Failed to mute %s in chat %s: %s", target.id, chat.id, exc)
            await message.reply_text(
                f"🟥 {display_name(target)} получает красную карточку (всего красных: {red}), "
                "но заглушить не удалось — дайте боту права администратора "
                "с разрешением «Ограничивать участников» (и убедитесь, что цель не админ)."
            )
    else:
        await message.reply_text(
            f"🟨 {display_name(target)} получает жёлтую карточку ({yellow}/{YELLOW_THRESHOLD})."
        )


async def cards_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Эта команда работает только в групповых чатах.")
        return

    rows = storage.list_cards(chat.id)
    if not rows:
        await update.message.reply_text("Пока никто не получал карточек.")
        return

    lines = ["Карточки участников чата:"]
    for name, yellow, red in rows:
        lines.append(f"{name}: 🟨 {yellow} 🟥 {red}")

    await update.message.reply_text("\n".join(lines))


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN с токеном бота.")

    db_path = os.environ.get("DB_PATH", "cards.db")
    storage.init_db(db_path)

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("yellow", give_yellow))
    application.add_handler(CommandHandler("cards", cards_list))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
