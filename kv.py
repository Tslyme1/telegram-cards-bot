"""Minimal client for the Vercel KV / Upstash Redis REST API."""

import os

import requests


def _request(command: list) -> object:
    url = os.environ["KV_REST_API_URL"]
    token = os.environ["KV_REST_API_TOKEN"]
    resp = requests.post(
        url,
        json=command,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"KV error for {command}: {data['error']}")
    return data.get("result")


def incr(key: str) -> int:
    return int(_request(["INCR", key]))


def get(key: str):
    return _request(["GET", key])


def set(key: str, value, ex: int | None = None):
    command = ["SET", key, str(value)]
    if ex is not None:
        command += ["EX", str(ex)]
    return _request(command)


def ttl(key: str) -> int:
    return int(_request(["TTL", key]))


def sadd(key: str, member: str):
    return _request(["SADD", key, member])


def smembers(key: str) -> list:
    return _request(["SMEMBERS", key]) or []
