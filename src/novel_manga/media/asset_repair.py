"""Explicit reference repairs and redraw coordination; never dispatches video tasks."""
from __future__ import annotations

from pathlib import Path
import json
import shutil
import time
from PIL import Image
from ..util import atomic_write_json
from .common import log, sha256_text
from .asset_records import REPAIR_LOCK, load_privacy_ok
from .asset_style import AssetStyle

REDRAW_ORIGIN = "privacy-stylized-redraw"


REDRAW_WAIT_SECONDS = 180


STYLIZE_PROMPT = (
    "以参考图为唯一身份依据，把这张角色卡重绘成一眼可辨的中国3D国漫动画角色，不是真人：保持同一人的脸型、年龄段、发型、"
    "胡须、服装款式与配色、站姿和构图完全不变；眼睛略大、五官简化概括、皮肤光滑无毛孔无老年斑、皱纹用动画化的少量线条表现，"
    "布料和头发是干净的三维建模材质，柔和体积光；纯色简洁背景；禁止真人照片质感、真实人物肖像、写实皮肤纹理、文字、Logo或水印。"
)


STYLIZE_STRONGER = '；比上一版更强的卡通化：头身比略夸张、眼睛明显更大、脸型圆润、皮肤为纯色平光、完全没有真人质感'


def stylize_card(provider, path: Path) -> Path:
    """Park a near-photoreal card (with its sidecars) and redraw it as clearly animated 3D."""
    backup = path.with_suffix(".photoreal-rejected.jpeg")
    for suffix in ("", ".task.json", ".request.json"):
        source = path.with_suffix(path.suffix + suffix)
        if source.exists():
            target = backup.with_suffix(backup.suffix + suffix)
            target.unlink(missing_ok=True)
            source.rename(target)
    provider.create_image(STYLIZE_PROMPT, path, reference=backup)
    with Image.open(path) as image:
        image.load()
    atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {
        "origin": REDRAW_ORIGIN, "source": backup.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT),
        "request_sha256": sha256_text(STYLIZE_PROMPT + backup.name), "reason": "near-photoreal card",
    })
    return path


def wait_for_inflight_redraws(paths, timeout: float = REDRAW_WAIT_SECONDS) -> list[Path]:
    """A card missing while its .photoreal-rejected.jpeg backup exists is being
    redrawn by another thread or process; wait for it instead of failing (or,
    in the asset factory's case, regenerating a photoreal card in its place)."""
    waited: list[Path] = []
    deadline = time.monotonic() + timeout
    for path in paths:
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        if (path.parent / f".regenerated.{path.name}").exists():
            continue  # deliberately deleted by the card review; the factory will rebuild it
        while not path.is_file() and backup.exists():
            if time.monotonic() > deadline:
                # The redraw is late or keeps failing (an nsfw refusal, say).
                # A photoreal card beats no card and a failed episode: put the
                # backup in place and mark the fix as tried so nobody retries it;
                # a redraw that still lands later simply replaces the file.
                shutil.copy2(backup, path)
                (path.parent / f".regenerated.{path.stem}.txt").touch()
                log(f"redraw of {path.parent.name}/{path.name} did not finish within {timeout:.0f}s; using the photoreal backup")
                break
            if path not in waited:
                waited.append(path)
                log(f"waiting for in-flight redraw of {path.parent.name}/{path.name}")
            time.sleep(5)
    return waited


def repair_rejected_reference(ctx, clip: dict, index: int) -> list[str]:
    """Seedance named the offending image (content[N]); fix exactly that one.

    A location card that shows people is rebuilt from its own spec prompt
    with the empty-scene line; a character card is stylized, or stylized
    harder if it was stylized once already.  Each step happens once.
    """
    references = clip.get("references", [])
    if not 0 <= index < len(references):
        return []
    ref = references[index]
    if ref.get("role") == "voice":
        return []  # a refused reference voice has no card to repair
    path = ctx.novel_dir / ref["path"]
    label = f"{ref['asset_id']}/{path.name}"
    if ref["path"] in (set(getattr(ctx, "_ok_assets", set())) | load_privacy_ok(ctx.novel_dir)):
        log(f"privacy repair: {label} has rendered fine before; not redrawn, the rejection stands")
        return []
    with REPAIR_LOCK:
        if ref["role"] != "character":
            marker = path.parent / ".emptied.txt"
            if marker.exists() or not path.is_file():
                return []
            spec = json.loads((path.parent / "spec.json").read_text(encoding="utf-8")) if (path.parent / "spec.json").is_file() else {}
            prompt = str(spec.get("prompt") or "") + getattr(ctx, "asset_style", AssetStyle()).location_empty_suffix
            for suffix in ("", ".task.json", ".request.json"):
                source = path.with_suffix(path.suffix + suffix)
                if source.exists():
                    target = path.with_suffix(".with-people" + path.suffix + suffix)
                    target.unlink(missing_ok=True)
                    source.rename(target)
            log(f"privacy repair: rebuilding {label} as an empty scene (people were read as a real person)")
            ctx.provider.create_image(prompt, path)
            with Image.open(path) as image:
                image.load()
            marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
            return [label]
        backup = path.with_suffix(".photoreal-rejected.jpeg")
        second = path.with_suffix(".photoreal-rejected2.jpeg")
        if not path.is_file() or second.exists():
            return []
        if not backup.exists():
            log(f"privacy repair: redrawing {label} as stylized 3D")
            stylize_card(ctx.provider, path)
            return [label]
        log(f"privacy repair: {label} was stylized once and still read as a real person; stylizing harder")
        for suffix in ("", ".task.json", ".request.json"):
            source = path.with_suffix(path.suffix + suffix)
            if source.exists():
                target = second.with_suffix(second.suffix + suffix)
                target.unlink(missing_ok=True)
                source.rename(target)
        ctx.provider.create_image(STYLIZE_PROMPT + STYLIZE_STRONGER, path, reference=second)
        with Image.open(path) as image:
            image.load()
        atomic_write_json(path.with_suffix(path.suffix + ".request.json"), {"origin": REDRAW_ORIGIN, "source": second.name, "prompt_sha256": sha256_text(STYLIZE_PROMPT + STYLIZE_STRONGER), "request_sha256": sha256_text(STYLIZE_PROMPT + STYLIZE_STRONGER + second.name), "reason": "second privacy rejection"})
        return [label]


def repair_privacy_cards(ctx, clip: dict) -> list[str]:
    """Redraw the character cards unique to a rejected clip as clearly animated.

    Seedance's privacy detector treats a near-photoreal CG face as a real
    person.  Cards (individual views) already used by a clip that generated
    fine are exempt, and when every card of the clip is exempt nothing is
    redrawn: the rejection stands and the clip fails.  The old fallback
    ("then it must be their combination, so all of them are candidates")
    restyled 雾月's protagonist on 2026-09-13 after 1,700 episodes had used
    his card - a changed face is worse than a failed clip.  A card is
    redrawn at most once: one already stylized (by this run, a parallel
    thread or another process) just earns the clip its retry.
    """
    exempt = set(getattr(ctx, "_ok_assets", set())) | load_privacy_ok(ctx.novel_dir)
    cards = [ref for ref in clip.get("references", []) if ref["role"] == "character"]
    candidates = [ref for ref in cards if ref["path"] not in exempt]
    if cards and not candidates:
        log(f"privacy repair: every card of {clip.get('clip_id')} has rendered fine before; none is redrawn, the rejection stands")
        return []
    repaired: list[str] = []
    with REPAIR_LOCK:
        for ref in candidates:
            path = ctx.novel_dir / ref["path"]
            label = f"{ref['asset_id']}/{path.name}"
            backup = path.with_suffix(".photoreal-rejected.jpeg")
            if not path.is_file() and backup.exists():
                wait_for_inflight_redraws([path])
                repaired.append(label)
                continue
            if not path.is_file():
                continue
            if backup.exists():
                # Live card next to a parked original = already stylized
                # once (the factory of a later run may have overwritten the
                # request.json marker, the backup file it cannot touch).
                repaired.append(label)
                continue
            log(f"privacy repair: redrawing {label} as stylized 3D from {backup.name}")
            stylize_card(ctx.provider, path)
            repaired.append(label)
    return repaired


