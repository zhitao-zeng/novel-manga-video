"""Is a location card the empty room it is meant to be?

The judge counts the people in it, and the answer is kept in the card's request record together with the
hash of the picture it was about, so a picture replaced by hand is not judged by an old count.  Calibrated
on nine cards with known answers (agent ch12, 2026-09-26): nine right with exact counts, and posters or
photos of people told apart from people in the room - those are counted separately and stop nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import ask_json, image_part, obj
from ..util import atomic_write_json
from .common import sha256_file

QUESTION = ("这是一张场景设定图，本该空无一人。请只数你真正看得见的，不要猜：\n"
            "people_in_scene：场景空间里实际存在的人，包括站着、坐着、走动的人，背影、剪影、倒影、车里的乘客、远处很小的人影；\n"
            "pictures_of_people：墙上海报、照片、画框、屏幕里画着的人像（这些不算在场景里的人）。\n"
            "where 用一句话说在哪里看到的；都没有就写“无”。")
SCHEMA = obj({"people_in_scene": {"type": "integer"}, "pictures_of_people": {"type": "integer"},
              "where": {"type": "string"}})


def people_in_card(card: Path) -> dict:
    return ask_json([image_part(card, 1280), {"type": "text", "text": QUESTION}], SCHEMA,
                    name="card_people", max_tokens=300, timeout=180)


def _record(card: Path) -> Path:
    return card.with_suffix(card.suffix + ".request.json")


def record_people_check(card: Path, verdict: dict) -> None:
    meta = _record(card)
    saved = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    saved["people_check"] = {**verdict, "artifact_sha256": sha256_file(card)}
    atomic_write_json(meta, saved)


def people_found(card: Path) -> int:
    """People the judge counted in this very picture: 0 if it was never counted or has since changed."""
    meta = _record(card)
    if not card.is_file() or not meta.is_file():
        return 0
    check = json.loads(meta.read_text(encoding="utf-8")).get("people_check") or {}
    if check.get("artifact_sha256") != sha256_file(card):
        return 0
    return int(check.get("people_in_scene") or 0)
