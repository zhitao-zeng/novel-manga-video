#!/usr/bin/env python3
"""Hand-written episode plans for ftj-s1 (焚天纪 chapters 1-3).

The machine keeps the factual layer -- story bible and chapter diagnosis --
because extraction is what these planners do reliably.  The creative layer
below is written by hand: retention beats, information gaps, character state
deltas and every shot.  Series state is chained episode to episode so the
adaptation carries its own continuity rather than restarting each chapter.

Run with the production .env sourced:
    set -a; source .env; set +a
    .venv/bin/python scripts/build_s1.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path("/mnt/disk1/zengzhitao/novel-manga-video")
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import ChapterDiagnosis, EpisodePlan, SeriesState, StoryBible
from novel_manga.script_planning import deterministic_series_state, evaluate_script_quality
from novel_manga.util import atomic_write_json

NOVEL_ID = "ftj-s1"
TITLE = "焚天纪"
SOURCE = ROOT / "outputs/inputs/ftj-s1-三章.txt"
SNAP = Path("/mnt/disk1/zengzhitao/tmp/ftj-s1-snapshot")
OUT_NOVEL = ROOT / "outputs" / NOVEL_ID
POLICY = "novel-manga-plan-v3-script-quality"

YAN, MEI, XUN, ZHAN = "楚焱", "楚媚", "楚烟儿", "楚战"
ELDER, YOUTH, MAIDEN, BUTLER = "月白老者", "月白青年", "月白少女", "墨管家"
TESTER = "测验员"

B = [f"beat_{i:03d}" for i in range(1, 9)]
F = [f"fact_{i:03d}" for i in range(1, 13)]

DS = {
    "social_status": "未明确", "relationship_state": "未明确", "power_level": "未明确",
    "emotional_state": "未明确", "confidence_state": "未明确", "costume_state": "沿用角色资产",
}


# ------------------------------------------------------------------ helpers
def turn(speaker, text, quote, *, mode, derivation, emotion="克制自然"):
    if speaker == "旁白":
        role, speaking = "narrator", False
    else:
        role, speaking = speaker, mode == "visible_dialogue"
    return {
        "role": role, "speaker_name": speaker, "text": text, "speaking": speaking,
        "delivery_mode": mode, "emotion": emotion, "source_quote": quote,
        "derivation": derivation,
    }


def perf(objective, start, trigger, action, reaction, end):
    return {
        "objective": objective, "start_state": start,
        "motion_beats": [{
            "phase": "development", "trigger": trigger, "action": action,
            "reaction": reaction, "expression_transition": "随动作自然过渡",
        }],
        "end_state": end,
    }


def cam(framing, axis, direction, *, motivation=None, trajectory=None, end=None):
    moving = motivation is not None
    return {
        "mode": "motivated_subtle" if moving else "locked",
        "motivation": motivation or "人物表演承担画面动态",
        "action_axis": axis, "screen_direction": direction,
        "start_position": framing,
        "camera_beats": [{
            "phase": "development",
            "trajectory": trajectory or "固定",
            "framing": framing,
            "parallax": "前景缓慢掠过" if moving else "背景适度虚化",
        }],
        "end_position": end or framing,
    }


def audio(beat_id, beats, *, ambience, sfx=None, energy=0.5):
    return {
        "speech_strategy": "locked", "voice_reference_id": "",
        "delivery_intent": "服务当前戏剧节拍", "pace": "自然", "energy": energy,
        "pauses": [], "music_cue": "", "ambience": ambience, "sfx_events": sfx or [],
        "audio_beats": [
            {"position_ratio": p, "cue_type": t, "cue": c, "trigger": g, "retention_beat_id": beat_id}
            for p, t, c, g in beats
        ],
        "ducking": True,
    }


def delta(name, events, before, after, quote, visual, performance):
    b, a = dict(DS), dict(DS)
    b.update(before)
    a.update(after)
    return {
        "character_name": name, "event_ids": events, "before": b, "after": a,
        "source_quote": quote, "visual_consequence": visual,
        "performance_consequence": performance,
    }


def beat(bid, func, s, e, question, promise, facts, events, shift, shots, quote):
    return {
        "beat_id": bid, "function": func, "target_start_ratio": s, "target_end_ratio": e,
        "audience_question": question, "promise": promise,
        "new_information_fact_ids": facts, "emotional_shift": shift,
        "event_ids": events, "shot_indexes": shots, "source_quote": quote,
    }


def fact(fid, statement, truth, viewer, use, awareness, events, quote, reveal):
    return {
        "fact_id": fid, "statement": statement, "truth_status": truth,
        "viewer_awareness": viewer, "dramatic_use": use,
        "character_awareness": [
            {"character_name": n, "awareness": a, "belief": bel}
            for n, a, bel in awareness
        ],
        "source_event_ids": events, "source_quote": quote, "reveal_beat_id": reveal,
    }


class ShotBuilder:
    def __init__(self, location):
        self.location = location
        self.shots = []

    def add(self, *, beat, func, strategy, narration, quote, events, turns, visual,
            motion, characters, scale, power, emo, focus, facts=None, camera=None,
            kf=None, audio_plan=None, performance=None, location=None):
        index = len(self.shots) + 1
        self.shots.append({
            "index": index,
            "narration": narration[:80],
            "subtitle": turns[0]["text"][:80],
            "visual_prompt": visual,
            "motion_prompt": motion,
            "characters": characters,
            "location": location or self.location,
            "source_quote": quote[:120],
            "scene_job": narration[:40],
            "event_ids": events,
            "shot_scale": scale,
            "turns": turns,
            "performance_plan": performance or perf(
                "完成当前叙事节拍", "上一拍收势", "台词或声音进入",
                "符合台词的最小动作", "对方或环境的自然反应", "为下一拍留势"),
            "camera_plan": camera or cam(f"{scale}固定机位", self.axis, self.direction),
            "visual_strategy": strategy,
            "keyframe_reasons": kf or [],
            "shot_intent": {
                "dramatic_function": func, "power_relation": power,
                "emotion_target": emo, "information_fact_ids": facts or [],
                "viewer_focus": focus, "retention_beat_id": beat,
            },
            "audio_plan": audio_plan or audio(
                beat, [(0.0, "ambience", "场景环境底噪", "镜头开始")], ambience="场景环境底噪"),
        })
        return index


def ledger(diagnosis, shots, externalized=(), removed=()):
    rows = []
    for event in diagnosis.events:
        eid = event.event_id
        indexes = [s["index"] for s in shots if eid in s["event_ids"]]
        if eid in removed:
            disp, why = "removed", "纯设定说明，改由人物处境和对白自然带出"
        elif eid in externalized:
            disp, why = "externalized", "叙述改写为在场角色的对白与反应"
        else:
            disp, why = "preserved", "关键事件按原文顺序呈现"
        rows.append({
            "event_id": eid, "disposition": disp,
            "shot_indexes": indexes, "rationale": why,
        })
    return rows


def digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def write_bundle(settings, bible, episode, diagnosis, plan, report, state, previous_state):
    episode_dir = OUT_NOVEL / f"{NOVEL_ID}_{episode.index}"
    episode_dir.mkdir(parents=True, exist_ok=True)
    planner_identity = {
        "planner_backend": settings.planner_backend,
        "planner_command_sha256": (
            hashlib.sha256(settings.planner_command.encode("utf-8")).hexdigest()
            if settings.planner_command else None),
        "llm_base_url_sha256": (
            hashlib.sha256(settings.llm_base_url.encode("utf-8")).hexdigest()
            if settings.llm_base_url else None),
        "llm_model": settings.llm_model,
        "planner_max_revisions": settings.planner_max_revisions,
        "planning_policy_revision": POLICY,
    }
    payload = {
        **planner_identity,
        "episode_index": episode.index,
        "source_sha256": hashlib.sha256(episode.source_text.encode("utf-8")).hexdigest(),
        "style_fingerprint": bible.style_fingerprint,
        "previous_state_sha256": digest(
            previous_state.model_dump(mode="json") if previous_state else {}),
    }
    atomic_write_json(episode_dir / "chapter_diagnosis.json", diagnosis.model_dump(mode="json"))
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    atomic_write_json(episode_dir / "script_quality_report.json", report.model_dump(mode="json"))
    atomic_write_json(episode_dir / "updated_series_state.json", state.model_dump(mode="json"))
    atomic_write_json(episode_dir / "episode_plan.json.request.json", {
        **payload, "request_sha256": digest(payload),
        "artifact_sha256": hashlib.sha256((episode_dir / "episode_plan.json").read_bytes()).hexdigest(),
        "origin": "manual-hand-written-season",
    })
    return episode_dir


# --------------------------------------------------------------------- main
def main() -> int:
    import ep1_data, ep2_data, ep3_data

    settings = Settings.from_env(
        provider="phanrouter", output_root=str(ROOT / "outputs"), admission_mode="production")
    novel = read_novel(SOURCE, novel_id=NOVEL_ID, title=TITLE)
    bible = StoryBible.model_validate_json((SNAP / "story_bible.json").read_text(encoding="utf-8"))
    locations = list(bible.locations or [])

    OUT_NOVEL.mkdir(parents=True, exist_ok=True)
    bible_path = OUT_NOVEL / "story_bible.json"
    shutil.copy2(SNAP / "story_bible.json", bible_path)
    planner_identity = {
        "planner_backend": settings.planner_backend,
        "planner_command_sha256": (
            hashlib.sha256(settings.planner_command.encode("utf-8")).hexdigest()
            if settings.planner_command else None),
        "llm_base_url_sha256": (
            hashlib.sha256(settings.llm_base_url.encode("utf-8")).hexdigest()
            if settings.llm_base_url else None),
        "llm_model": settings.llm_model,
        "planner_max_revisions": settings.planner_max_revisions,
        "planning_policy_revision": POLICY,
    }
    bible_payload = {
        **planner_identity, "novel_id": novel.novel_id, "novel_title": novel.title,
        "source_sha256": hashlib.sha256(novel.text.encode("utf-8")).hexdigest(),
    }
    atomic_write_json(bible_path.with_suffix(bible_path.suffix + ".request.json"), {
        **bible_payload, "request_sha256": digest(bible_payload),
        "artifact_sha256": hashlib.sha256(bible_path.read_bytes()).hexdigest(),
        "origin": "manual-hand-written-season",
    })
    print("bible:", bible.style_fingerprint, [c.name for c in bible.characters])

    modules = {1: ep1_data, 2: ep2_data, 3: ep3_data}
    default_location = {1: locations[0] if locations else "测验广场",
                        2: locations[1] if len(locations) > 1 else "山崖之巅",
                        3: locations[3] if len(locations) > 3 else "迎客大厅"}

    previous_state: SeriesState | None = None
    ok = True
    for episode in novel.episodes:
        mod = modules[episode.index]
        src_norm = episode.source_text.replace(" ", "")
        for line in mod.LINES:
            if line.replace(" ", "") not in src_norm:
                print(f"  ep{episode.index} LINE MISSING: {line[:36]}")
                ok = False
        diagnosis = ChapterDiagnosis.model_validate_json(
            (SNAP / f"chapter_diagnosis_ep{episode.index}.json").read_text(encoding="utf-8"))
        shots = mod.build(default_location[episode.index])
        plan_payload = {
            "video_title": mod.TITLE_TEXT, "hook": mod.HOOK, "summary": mod.SUMMARY,
            "shots": shots, "next_preview": mod.PREVIEW,
            "adaptation_ledger": ledger(diagnosis, shots, mod.EXTERNALIZED, mod.REMOVED),
            "creative_profile": "short-drama-adaptive-v1",
            "dramaturgy": mod.DRAMATURGY, "showrunner_plan": mod.SHOWRUNNER,
        }
        by_beat: dict[str, list[int]] = {}
        for s in shots:
            by_beat.setdefault(s["shot_intent"]["retention_beat_id"], []).append(s["index"])
        for bt in plan_payload["showrunner_plan"]["retention"]["beats"]:
            bt["shot_indexes"] = by_beat.get(bt["beat_id"], [])
        plan = EpisodePlan.model_validate(plan_payload)
        report = evaluate_script_quality(plan, diagnosis, episode, previous_state=previous_state)
        turns = sum(len(s["turns"]) for s in shots)
        dv = sum(1 for s in shots for t in s["turns"]
                 if t["derivation"] == "derived" and t["role"] != "narrator")
        print("ep%d %-8s passed=%-5s chars=%-4d shots=%-3d turns=%-3d derived角色对白=%-3d narr=%.3f delta=%.2f"
              % (episode.index, mod.TITLE_TEXT, report.passed, report.script_char_count,
                 report.shot_count, turns, dv, report.narration_ratio,
                 report.character_delta_grounding))
        for issue in report.issues:
            print("     [%s] shots=%s %s" % (issue.code, issue.shot_indexes or "-", issue.message[:70]))
            ok = False
        if not report.passed:
            ok = False
            continue
        state = deterministic_series_state(episode, diagnosis, previous_state)
        write_bundle(settings, bible, episode, diagnosis, plan, report, state, previous_state)
        previous_state = state
    print("ALL PASSED" if ok else "HAS ISSUES")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
