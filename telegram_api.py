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


def _call(method: str, **params) -> dict:
    token = os.environ["BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=params, timeout=10)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error on {method}: {data}")
    return data["result"]


def send_message(chat_id, text: str, reply_to_message_id: int | None = None) -> dict:
    params = {"chat_id": chat_id, "text": text}
    if reply_to_message_id is not None:
        params["reply_to_message_id"] = reply_to_message_id
    return _call("sendMessage", **params)


def restrict_chat_member(chat_id, user_id, until_date: int) -> dict:
    return _call(
        "restrictChatMember",
        chat_id=chat_id,
        user_id=user_id,
        permissions=MUTE_PERMISSIONS,
        until_date=until_date,
    )
