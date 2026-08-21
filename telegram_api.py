"""Minimal Telegram Bot API client using plain HTTP calls."""

import os

import requests

MUTE_PERMISSIONS = {
    "can_send_messages": False,
    "can_send_audios": False,
    "can_send_documents": False,
    "can_send_photos": False,
    "can_send_videos": False,
    "can_send_video_notes": False,
    "can_send_voice_notes": False,
    "can_send_polls": False,
    "can_send_other_messages": False,
    "can_add_web_page_previews": False,
}

MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


def _call(method: str, **params) -> dict:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=params, timeout=10)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error on {method}: {data}")
    return data["result"]


def send_message(chat_id, text: str, reply_to_message_id=None, reply_markup=None) -> dict:
    params = {"chat_id": chat_id, "text": text}
    if reply_to_message_id is not None:
        params["reply_to_message_id"] = reply_to_message_id
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _call("sendMessage", **params)


def edit_message_text(chat_id, message_id, text: str, reply_markup=None) -> dict:
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _call("editMessageText", **params)


def answer_callback_query(callback_query_id, text: str | None = None) -> dict:
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
    return _call("answerCallbackQuery", **params)


def restrict_chat_member(chat_id, user_id, until_date: int) -> dict:
    return _call(
        "restrictChatMember",
        chat_id=chat_id,
        user_id=user_id,
        permissions=MUTE_PERMISSIONS,
        until_date=until_date,
    )


def get_chat_member_status(chat_id, user_id) -> str | None:
    try:
        return _call("getChatMember", chat_id=chat_id, user_id=user_id).get("status")
    except Exception:
        return None


def is_chat_member(chat_id, user_id) -> bool:
    return get_chat_member_status(chat_id, user_id) in MEMBER_STATUSES


def get_chat_administrators(chat_id) -> list:
    return _call("getChatAdministrators", chat_id=chat_id)


def set_chat_member_tag(chat_id, user_id, tag: str = "") -> dict:
    """Set (or, with an empty tag, clear) a regular member's tag.

    Unlike an admin's custom title, this needs no promotion — Bot API 9.5's
    setChatMemberTag works directly on any member. The bot itself still
    needs to be an administrator with the can_manage_tags right.

    user_id is coerced to an int: ids travel through the dialog state as
    strings, and this method is stricter about the type than the older ones.
    """
    return _call("setChatMemberTag", chat_id=chat_id, user_id=int(user_id), tag=tag)


def get_me() -> dict:
    return _call("getMe")
