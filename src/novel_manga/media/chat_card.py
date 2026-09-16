#!/usr/bin/env python
"""Render phone chat screens (group and private) as short video segments.

The video model cannot write legible Chinese, so chat messages are drawn here
instead: a WeChat-like screen is composed with PIL, one still per message state,
and the stills are joined into a 3-7 s segment with a soft chime per message.
The runner inserts that segment right before the clip whose stage the messages
belong to, so the audience reads the messages and then sees the reaction.

Avatars are cropped from the character cards in ``series_assets`` so the faces
on screen are the same faces as in the film; a character without a card gets a
coloured initial.

    python chat_card.py --novel-dir outputs/X --episode-dir outputs/X/X_11 [--preview]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# The screen is composed at a fixed internal size and scaled into the frame, so
# every layout number below is independent of the delivery resolution.
SCREEN_W, SCREEN_H = 900, 1600  # 9:16 keeps the text large once the phone is scaled into the frame
BG = (237, 237, 237)
BUBBLE_OTHER = (255, 255, 255)
BUBBLE_SELF = (149, 236, 105)
TEXT_COLOUR = (24, 24, 24)
NICK_COLOUR = (150, 150, 150)
BAR_COLOUR = (245, 245, 247)
LINE_COLOUR = (214, 214, 216)
AVATAR = 104
GAP = 26
PAD_X, PAD_Y = 26, 20
RADIUS = 16
BUBBLE_MAX = int(SCREEN_W * 0.60)
TITLE_H, STATUS_H, INPUT_H = 108, 64, 120
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
AVATAR_PALETTE = [(90, 143, 214), (214, 129, 90), (117, 178, 121), (176, 122, 196), (206, 158, 78), (110, 168, 178)]
SECONDS_PER_MESSAGE = 1.15
# Gain on the notification tone: 2.5 lands the chime near -13 dB peak, which
# sits under dialogue (about -3 dB) but is clearly audible; 0.22 was inaudible.
CHIME_GAIN = 2.5
MIN_SECONDS, MAX_SECONDS = 3.0, 7.0


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for character in text:
        if character == "\n":
            lines.append(current)
            current = ""
            continue
        if draw.textlength(current + character, font=face) > width and current:
            lines.append(current)
            current = character
        else:
            current += character
    if current or not lines:
        lines.append(current)
    return lines


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.size[0] - 1, image.size[1] - 1), radius=radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def letter_avatar(name: str, size: int = AVATAR) -> Image.Image:
    colour = AVATAR_PALETTE[int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % len(AVATAR_PALETTE)]
    tile = Image.new("RGB", (size, size), colour)
    draw = ImageDraw.Draw(tile)
    face = font(int(size * 0.52))
    letter = (name or "?")[0]
    box = draw.textbbox((0, 0), letter, font=face)
    draw.text(((size - box[2] + box[0]) / 2 - box[0], (size - box[3] + box[1]) / 2 - box[1]), letter, font=face, fill=(255, 255, 255))
    return rounded(tile, int(size * 0.18))


def card_avatar(card: Path, size: int = AVATAR) -> Image.Image:
    """Crop the head out of a full-body character card.

    Every card is generated with the same framing (one standing figure, head at
    the top centre), so a fixed window is enough and needs no face detector.
    """
    with Image.open(card).convert("RGB") as source:
        width, height = source.size
        side = int(height * 0.19)
        centre_x, centre_y = width // 2, int(height * 0.115)
        box = (
            max(0, centre_x - side // 2), max(0, centre_y - side // 2),
            min(width, centre_x + side // 2), min(height, centre_y + side // 2),
        )
        head = source.crop(box).resize((size, size), Image.LANCZOS)
    return rounded(head, int(size * 0.18))


def load_avatars(novel_dir: Path, names: list[str], cache_dir: Path | None = None) -> dict[str, Image.Image]:
    """Name → avatar, cropped from the character card when the character has one."""
    manifest_path = novel_dir / "series_assets" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    cards = {
        str(row.get("name")): novel_dir / "series_assets" / "characters" / str(row.get("asset_id")) / "turnaround.jpeg"
        for row in manifest.get("characters", [])
    }
    cache_dir = cache_dir or (novel_dir / "series_assets" / "avatars")
    cache_dir.mkdir(parents=True, exist_ok=True)
    avatars: dict[str, Image.Image] = {}
    for name in names:
        card = cards.get(name)
        from_card = bool(card and card.is_file())
        # The cache name says where the avatar came from, so a character who
        # gets a card later is re-cropped instead of keeping the coloured initial.
        cached = cache_dir / f"{name}.{'card' if from_card else 'letter'}.png"
        if cached.is_file():
            avatars[name] = Image.open(cached).convert("RGBA")
            continue
        avatars[name] = card_avatar(card) if from_card else letter_avatar(name)
        avatars[name].save(cached)
    return avatars


def draw_screen(messages: list[dict], visible: int, *, title: str, self_name: str, group: bool, avatars: dict[str, Image.Image], clock: str = "9:41") -> Image.Image:
    """One still: the first ``visible`` messages of the conversation."""
    screen = Image.new("RGB", (SCREEN_W, SCREEN_H), BG)
    draw = ImageDraw.Draw(screen)
    name_font, text_font, title_font, small_font = font(30), font(40), font(46), font(28)

    draw.rectangle((0, 0, SCREEN_W, STATUS_H + TITLE_H), fill=BAR_COLOUR)
    draw.line((0, STATUS_H + TITLE_H, SCREEN_W, STATUS_H + TITLE_H), fill=LINE_COLOUR, width=2)
    draw.text((36, 18), clock, font=small_font, fill=(40, 40, 40))
    for index, height in enumerate((10, 16, 22, 28)):  # signal bars
        draw.rectangle((SCREEN_W - 150 + index * 12, 40 - height, SCREEN_W - 144 + index * 12, 40), fill=(40, 40, 40))
    draw.rounded_rectangle((SCREEN_W - 92, 16, SCREEN_W - 40, 42), radius=6, outline=(40, 40, 40), width=2)
    draw.rectangle((SCREEN_W - 89, 19, SCREEN_W - 55, 39), fill=(40, 40, 40))
    heading = f"{title}({len(_participants(messages, self_name))})" if group else title
    box = draw.textbbox((0, 0), heading, font=title_font)
    draw.text(((SCREEN_W - box[2]) / 2, STATUS_H + (TITLE_H - box[3]) / 2), heading, font=title_font, fill=(20, 20, 20))
    draw.polygon([(46, STATUS_H + TITLE_H // 2), (66, STATUS_H + TITLE_H // 2 - 16), (66, STATUS_H + TITLE_H // 2 + 16)], fill=(60, 60, 60))

    rows = []
    for message in messages[:visible]:
        who, text = str(message.get("speaker_name", "")), str(message.get("text", "")).strip()
        mine = who == self_name
        lines = wrap(draw, text, text_font, BUBBLE_MAX - 2 * PAD_X)
        line_height = text_font.getbbox("字")[3] + 12
        bubble_w = int(max(draw.textlength(line, font=text_font) for line in lines)) + 2 * PAD_X
        bubble_h = line_height * len(lines) + 2 * PAD_Y
        nickname = (not mine) and group
        rows.append({"who": who, "mine": mine, "lines": lines, "w": bubble_w, "h": bubble_h, "nick": nickname,
                     "height": bubble_h + (name_font.getbbox("字")[3] + 10 if nickname else 0) + GAP, "lh": line_height})

    list_top, list_bottom = STATUS_H + TITLE_H + 20, SCREEN_H - INPUT_H - 20
    total = sum(row["height"] for row in rows)
    y = max(list_top, list_bottom - total)  # a chat sits at the bottom of the screen and scrolls up
    for row in rows:
        avatar = avatars.get(row["who"]) or letter_avatar(row["who"])
        top = y
        if row["nick"]:
            draw.text((AVATAR + 2 * GAP, top), row["who"], font=name_font, fill=NICK_COLOUR)
            top += name_font.getbbox("字")[3] + 10
        if row["mine"]:
            avatar_x = SCREEN_W - GAP - AVATAR
            bubble_x = avatar_x - GAP - row["w"]
            colour = BUBBLE_SELF
        else:
            avatar_x = GAP
            bubble_x = avatar_x + AVATAR + GAP
            colour = BUBBLE_OTHER
        screen.paste(avatar, (avatar_x, top), avatar)
        draw.rounded_rectangle((bubble_x, top, bubble_x + row["w"], top + row["h"]), radius=RADIUS, fill=colour)
        tail_y = top + 26
        if row["mine"]:
            draw.polygon([(bubble_x + row["w"], tail_y - 8), (bubble_x + row["w"] + 12, tail_y), (bubble_x + row["w"], tail_y + 8)], fill=colour)
        else:
            draw.polygon([(bubble_x, tail_y - 8), (bubble_x - 12, tail_y), (bubble_x, tail_y + 8)], fill=colour)
        for index, line in enumerate(row["lines"]):
            draw.text((bubble_x + PAD_X, top + PAD_Y + index * row["lh"]), line, font=text_font, fill=TEXT_COLOUR)
        y += row["height"]

    draw.rectangle((0, SCREEN_H - INPUT_H, SCREEN_W, SCREEN_H), fill=BAR_COLOUR)
    draw.line((0, SCREEN_H - INPUT_H, SCREEN_W, SCREEN_H - INPUT_H), fill=LINE_COLOUR, width=2)
    draw.ellipse((GAP, SCREEN_H - INPUT_H + 30, GAP + 56, SCREEN_H - INPUT_H + 86), outline=(90, 90, 90), width=3)
    draw.rounded_rectangle((GAP + 76, SCREEN_H - INPUT_H + 26, SCREEN_W - GAP - 140, SCREEN_H - 26), radius=12, fill=(255, 255, 255))
    draw.ellipse((SCREEN_W - GAP - 120, SCREEN_H - INPUT_H + 30, SCREEN_W - GAP - 64, SCREEN_H - INPUT_H + 86), outline=(90, 90, 90), width=3)
    draw.ellipse((SCREEN_W - GAP - 56, SCREEN_H - INPUT_H + 30, SCREEN_W - GAP, SCREEN_H - INPUT_H + 86), outline=(90, 90, 90), width=3)
    return screen


def _participants(messages: list[dict], self_name: str) -> set[str]:
    people = {str(m.get("speaker_name", "")) for m in messages}
    people.add(self_name)
    return {p for p in people if p}


def compose_frame(screen: Image.Image, width: int, height: int, background: Path | None) -> Image.Image:
    """Put the phone screen on a blurred still from the scene it belongs to."""
    if background and Path(background).is_file():
        with Image.open(background).convert("RGB") as source:
            scale = max(width / source.width, height / source.height)
            plate = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.LANCZOS)
            left, top = (plate.width - width) // 2, (plate.height - height) // 2
            frame = plate.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(26))
        frame = Image.blend(frame, Image.new("RGB", (width, height), (10, 12, 18)), 0.62)  # the screen has to be the brightest thing in frame
    else:
        frame = Image.new("RGB", (width, height), (16, 18, 26))
        painter = ImageDraw.Draw(frame)
        for y in range(height):
            shade = 16 + int(26 * y / max(1, height))
            painter.line((0, y, width, y), fill=(shade, shade + 2, shade + 8))

    phone_h = int(height * (0.90 if height > width else 0.94))
    phone_w = round(phone_h * SCREEN_W / SCREEN_H)
    if phone_w > width * 0.9:
        phone_w = round(width * 0.9)
        phone_h = round(phone_w * SCREEN_H / SCREEN_W)
    body = Image.new("RGBA", (phone_w + 26, phone_h + 26), (0, 0, 0, 0))
    ImageDraw.Draw(body).rounded_rectangle((0, 0, phone_w + 25, phone_h + 25), radius=48, fill=(22, 22, 26, 255))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left, top = (width - phone_w - 26) // 2, (height - phone_h - 26) // 2
    shadow.paste(body, (left, top), body)
    frame = Image.alpha_composite(frame.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(18)))
    frame = Image.alpha_composite(frame, _placed(body, width, height, left, top))
    inner = rounded(screen.resize((phone_w, phone_h), Image.LANCZOS), 36)
    frame = Image.alpha_composite(frame, _placed(inner, width, height, left + 13, top + 13))
    return frame.convert("RGB")


def _placed(image: Image.Image, width: int, height: int, left: int, top: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(image, (left, top), image)
    return canvas


MESSAGES_PER_CARD = 5


def windows(count: int, size: int = MESSAGES_PER_CARD) -> list[tuple[int, int]]:
    """Split a conversation into cards of at most ``size`` new messages each.

    Card k opens with everything said so far already on screen and pops only its
    own messages, so a long exchange scrolls on naturally across several cards.
    """
    return [(start, min(count, start + size)) for start in range(0, max(1, count), size)]


def build_segment(messages: list[dict], output: Path, *, title: str, self_name: str, group: bool, avatars: dict[str, Image.Image],
                  width: int, height: int, fps: int, background: Path | None = None, work: Path | None = None,
                  window: tuple[int, int] | None = None) -> tuple[Path, float]:
    """Render one card and encode it as a video segment; returns (path, seconds)."""
    work = work or output.parent / f"{output.stem}_frames"
    work.mkdir(parents=True, exist_ok=True)
    first, last = window or (0, len(messages))
    count = max(1, last - first)
    per = min(SECONDS_PER_MESSAGE, max(0.7, MAX_SECONDS / max(1, count)))
    hold = max(0.0, MIN_SECONDS - per * count) + 0.9  # let the last message stay readable
    stills = []
    for offset, index in enumerate(range(first + 1, last + 1), start=1):
        frame = compose_frame(draw_screen(messages, index, title=title, self_name=self_name, group=group, avatars=avatars), width, height, background)
        path = work / f"state_{index:02d}.png"
        frame.save(path)
        stills.append((path, per + (hold if offset == count else 0.0)))
    total = round(sum(seconds for _, seconds in stills), 3)

    listing = work / "states.txt"
    lines = []
    for path, seconds in stills:
        lines.append(f"file '{path.name}'")
        lines.append(f"duration {seconds:.3f}")
    lines.append(f"file '{stills[-1][0].name}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

    command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    chains = []
    for index in range(count):  # one soft chime as each message lands
        command += ["-f", "lavfi", "-t", "0.5", "-i", "sine=frequency=1046:duration=0.5"]
        delay = round(index * per * 1000)
        chains.append(f"[{index + 1}:a]afade=t=out:st=0.03:d=0.35,volume={CHIME_GAIN},adelay={delay}|{delay}[c{index}]")
    chains.append("".join(f"[c{index}]" for index in range(count)) + f"amix=inputs={count}:normalize=0,apad[aout]")
    # The video filters live in the same graph as the audio: ffmpeg ignores -vf
    # when a filter_complex is present, which silently left the card a still
    # picture and tripped the freeze check.  The camera drifts across the card
    # and a little temporal grain keeps every frame different.
    chains.append(
        # fps first: the concat demuxer emits one frame per still, so the pan
        # and the grain have to run after the stream is filled out to full
        # rate, or every duplicated frame is identical and reads as a freeze.
        f"[0:v]fps={fps},scale=iw*1.08:ih*1.08,crop=w={width}:h={height}:"
        f"x='(iw-ow)/2':y='(ih-oh)/2',"  # fixed framing: the slow scroll and drift read as a fault on screen
        f"noise=alls=6:allf=t,format=yuv420p[vout]"
    )
    command += [
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", str(output),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return output, total


def channels(chat_lines: list[dict], self_name: str) -> list[dict]:
    """Split a clip's chat lines into consecutive runs that share one screen.

    ``chat_target`` empty means the group; a name means a one-to-one chat with
    that person, which gets its own screen titled with their name.
    """
    runs: list[dict] = []
    for line in chat_lines:
        target = str(line.get("chat_target") or "").strip()
        key = target or "__group__"
        if not runs or runs[-1]["key"] != key:
            runs.append({"key": key, "target": target, "messages": []})
        runs[-1]["messages"].append(line)
    return runs
