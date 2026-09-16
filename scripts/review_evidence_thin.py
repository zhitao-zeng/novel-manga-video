"""Load book evidence, character phases and review configuration; extract video frames."""
from __future__ import annotations

import json
from novel_manga.models import StoryBible
from novel_manga.review.evidence import ClipEvidence
import thin_phases
from identity_context_thin import prompt_block as identity_prompt_block
import os
import re
import subprocess
from pathlib import Path
import novel_manga.model_client as model_client
from novel_manga.review import policy
import novel_manga.review.contracts as review_contracts
from novel_manga.util import media_duration
from thin_profile import load_genre, load_profile


_SEGMENTS_CACHE: dict = {}


_LEDGER_CACHE: dict = {}


_NOVEL_TEXT_CACHE: dict = {}


def snapshot_block(clip: dict, episode_dir: Path) -> str:
    """The entity ledger's casting sheet for this clip's passage - who is on stage, through whose body, under
    which names, who is only a voice or only spoken of, how they stand to each other, what the audience must
    not learn yet.  Empty when the novel has no ledger or it has not read this chapter (nothing changes)."""
    novel_dir = Path(episode_dir).parent
    chapter = str(episode_dir.name).rsplit("_", 1)[-1]
    if not chapter.isdigit() or not (novel_dir / "entity" / "mentions" / f"ch_{int(chapter):04d}.json").is_file():
        return ""
    try:
        from ledger_store_thin import Ledger, novel_texts
        from ledger_views_thin import snapshot
        ledger = _LEDGER_CACHE.get(novel_dir) or _LEDGER_CACHE.setdefault(novel_dir, Ledger(novel_dir))
        texts = _NOVEL_TEXT_CACHE.get(novel_dir) or _NOVEL_TEXT_CACHE.setdefault(novel_dir, novel_texts(novel_dir))
        shot = snapshot(novel_dir, int(chapter), clip.get("segment_ids") or None, ledger=ledger, text=texts.get(int(chapter), ""))
    except Exception as error:  # noqa: BLE001 - the snapshot is an aid, never a reason to fail the review
        model_client.log(f"snapshot unavailable for {episode_dir.name}: {type(error).__name__}: {str(error)[:80]}")
        return ""
    if not shot.get("cast"):
        return ""

    def who(row: dict) -> str:
        text = row["name"]
        if row.get("acts_through_other_body"):
            text += f"（此时在{ledger.name_of(row['body'])}的身体里，画面应是那具身体的样子）"
        spoken = [n for n in row.get("names_spoken", []) if n != row["name"]]
        if spoken:
            text += f"（原文里也叫 {'/'.join(spoken[:4])}）"
        return text

    on = [who(r) for r in shot["cast"] if r["presence"] == "on_stage"]
    voice = [r["name"] for r in shot["cast"] if r["presence"] == "voice"]
    mentioned = [r["name"] for r in shot["cast"] if r["presence"] == "mentioned"]
    relations = [f"{r['from']}→{r['to']}：{'/'.join(r['bases']) or r['stance']}" + (f"（称呼 {'/'.join(r['address'])}）" if r["address"] else "")
                 for r in shot.get("relations", [])]
    secrets = [f"{s['subject']} {s['type']} {s['object']}" for s in shot.get("must_not_reveal", [])]
    return ("\n原著账本出场快照（这一段原文里）：在场 " + ("、".join(on) or "无")
            + (f"；只有声音 {'、'.join(voice)}" if voice else "") + (f"；只被提及、不在场 {'、'.join(mentioned)}" if mentioned else "")
            + (f"\n人物关系：{'；'.join(relations)}" if relations else "")
            + (f"\n本集观众尚不能知道：{'；'.join(secrets)}" if secrets else "") + "\n")


def source_contract_block(clip: dict, episode_dir: Path) -> str:
    """Carry verified attribution into subsequent video reviews, not just repair."""
    from novel_manga.runtime_backends import normalize_text
    try:
        facts = json.loads((episode_dir/'source_speaker_contract.json').read_text())
        source = normalize_text('\n'.join(segment_texts(episode_dir).values()))
    except (OSError, ValueError):
        return ''
    lines = clip.get('lines', [])
    bound = []
    from identity_store_thin import current_context
    identity_context = current_context(episode_dir)
    for row in facts:
        if identity_context and row.get('identity_policy') != identity_context['policy']:
            continue  # old attribution must not override a newer source identity reading
        quote = str(row.get('source_quote') or '')
        text = str(row.get('adapted_text') or '')
        if (row.get('stage') in clip.get('shot_indexes', []) and quote and text
                and normalize_text(quote) in source
                and any(line.get('speaker_name') == row.get('speaker')
                        and (normalize_text(text) in normalize_text(line.get('text',''))
                             or normalize_text(line.get('text','')) in normalize_text(text)) for line in lines)):
            bound.append({'speaker':row['speaker'],'dialogue':text,'relation':row.get('relation'),'source_quote':quote})
    if not bound:
        return ''
    return ('\n已逐条核验的台词归属（引用来自原文，后续复审继续使用）：'+json.dumps(bound,ensure_ascii=False)
            +'\n判断动作/说话者时先核对这份对应关系。若认为它与原文冲突，必须指出具体原文，不能凭旧判词改换说话者。\n')


def clip_frames(video: Path, output_dir: Path, count: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    seconds = media_duration(video)
    frames = []
    for index in range(count):
        position = seconds * (0.1 + 0.8 * index / max(1, count - 1))
        target = output_dir / f"frame_{index + 1}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{position:.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={review_contracts.FRAME_WIDTH}:-2", "-q:v", "4", str(target)], check=True)
        frames.append(target)
    return frames


def segment_texts(episode_dir: Path) -> dict[str, str]:
    """segment_id -> the book's own words for that stretch (segments.json), empty when the file is not there."""
    path = episode_dir / "segments.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        return {}
    return {str(row.get("segment_id")): str(row.get("text") or "") for row in rows if isinstance(row, dict)}


def bible_root(work_dir: Path) -> Path:
    # work_dir = <novel>/<episode>/work/review/<clip>; references are relative to <novel>
    return work_dir.parents[3]


def review_world_context(novel_dir: Path) -> str:
    """Use the book's existing normal-world notes in precise review as well."""
    path=Path(novel_dir)/'review_normal.txt'
    if not path.is_file():
        return ''
    normal='\n'.join(line for line in path.read_text(encoding='utf-8').splitlines() if line.strip() and not line.lstrip().startswith('#'))
    if not normal:
        return ''
    return ('\n本书美术说明（用于理解风格，不是人物身份与剧情的豁免）：\n'+normal+
            '\n本段原文、角色当前成长阶段和明确身份优先于笼统规则。原文或人设明确允许的拟人、变形、分身等不判为生成错误；'
            '这些设定不能为动作或台词安错人、遗漏必需角色、超出原文人数的复制开脱。\n')


def load_review_rules(novel_dir: Path) -> policy.ReviewRules:
    """Load an independent set of rules; an unconfigured genre uses defaults."""
    try:
        index = json.loads((Path(novel_dir) / "entity_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    tiers = tuple((row["name"], str(row.get("tier") or ""))
                  for row in index.get("characters", []) if row.get("name"))
    pattern = load_genre(load_profile(novel_dir)).get("breakdown_pattern")
    return policy.ReviewRules(re.compile(pattern) if pattern else policy.BREAKDOWN, tiers)


def review_mode(work_dir: Path) -> str:
    mode = os.environ.get("NOVEL_REVIEW_MODE", "").strip()
    if mode:
        return mode
    try:
        return str(load_profile(bible_root(work_dir)).get("review_mode") or "")
    except Exception:  # noqa: BLE001 - no profile, or one the loader rejects: the classic judge
        return ""


def collect_clip_evidence(clip: dict, video: Path, bible: StoryBible, work_dir: Path, *, verify: bool = False) -> tuple[list[dict], ClipEvidence]:
    """Select the same cards/frames and load the same evidence as the existing mode."""
    phases = thin_phases.load_phases(bible_root(work_dir))
    chapter = thin_phases.chapter_of(work_dir.parents[2])
    by_name = {c.name: thin_phases.phased(c, thin_phases.phase_for(phases, c.name, chapter)) for c in bible.characters}
    cast = [name for name in clip.get("cast", []) if name in by_name]
    extras = list(dict.fromkeys([*(clip.get("extras") or []), *(e for shot in clip.get("shots", []) for e in (shot.get("extras") or []))]))
    listeners = list(dict.fromkeys(l for l in [*(clip.get("listeners") or []), *(l for shot in clip.get("shots", []) for l in (shot.get("listeners") or []))] if l in by_name))
    offscreen = list(dict.fromkeys(str(row.get("speaker_name") or "") for row in clip.get("lines", []) if row.get("delivery_mode") == "offscreen_dialogue" and row.get("speaker_name")))
    cards = []
    for name in cast[:((3 if len(cast) <= 3 else 2) if verify else review_contracts.MAX_IMAGES - 3)]:
        path = next((Path(ref["path"]) for ref in clip.get("references", []) if ref.get("name") == name and (str(ref["path"]) if verify else ref["path"]).endswith("turnaround.jpeg")), None)
        # The plan may predate the character's phases: the judge sees the phase's card when it is drawn, so an
        # adult dragon is not marked down against the hatchling card (星海 洛恩, 254 clips on 2026-09-13).
        path = thin_phases.phase_card(bible_root(work_dir), phases, name, chapter) or path
        if path is not None and (bible_root(work_dir) / path).is_file():  # a card being rebuilt is simply not shown
            cards.append((name, path))
    # Clips with on-screen chat get more frames: stray text tends to flash briefly.
    frame_count = min(6 if clip.get("chat_lines") else review_contracts.FRAMES_PER_CLIP, review_contracts.MAX_IMAGES - len(cards))
    if verify:
        frame_count = review_contracts.MAX_IMAGES - len(cards)
    frames = clip_frames(video, work_dir, frame_count)
    parts = []
    legend = []
    for number, (name, path) in enumerate(cards, start=1):
        parts.append(model_client.image_part(bible_root(work_dir) / path, review_contracts.CARD_SIDE))
        legend.append(f"图{number}=角色卡：{name}")
    for number, frame in enumerate(frames, start=len(cards) + 1):
        parts.append(model_client.image_part(frame, review_contracts.FRAME_WIDTH))
        legend.append(f"图{number}=视频第{number - len(cards)}帧")
    episode = work_dir.parents[2]
    screen_path = bible_root(work_dir) / "chat_screen.json"
    screen = json.loads(screen_path.read_text(encoding="utf-8")) if not verify and screen_path.is_file() else {}
    segments = _SEGMENTS_CACHE.setdefault(episode, segment_texts(episode))
    snapshot = snapshot_block(clip, episode)
    contract = source_contract_block(clip, episode) if verify else ""
    world = review_world_context(bible_root(work_dir)) if verify else ""
    identity = identity_prompt_block(episode, cast) if verify else ""
    return parts, ClipEvidence(by_name, cast, extras, listeners, offscreen,
                               [name for name in clip.get("background_only", []) if name in by_name],
                               legend, segments, snapshot, contract, world, identity, screen)
