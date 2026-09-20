"""Counts and verbatim source coverage shared by validation and reports."""
from __future__ import annotations
from . import text as pc_text

def metrics(shots: list[dict], chapter_text: str, segments: list[dict], skipped: dict) -> dict:
    chapter_key = pc_text.quote_key(chapter_text)
    spoken = 0
    verbatim = 0
    turn_count = 0
    by_mode: dict[str, int] = {}
    speakers: dict[str, int] = {}
    for shot in shots:
        for turn in shot["turns"]:
            turn_count += 1
            by_mode[turn["delivery_mode"]] = by_mode.get(turn["delivery_mode"], 0) + 1
            if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}:
                chars = pc_text.spoken_chars(turn["text"])
                spoken += chars
                if pc_text.quote_key(turn["text"]) in chapter_key:
                    verbatim += chars
                speakers[turn["speaker_name"]] = speakers.get(turn["speaker_name"], 0) + 1
    coverage = {
        segment["segment_id"]: [shot["origin_index"] for shot in shots if shot["segment_id"] == segment["segment_id"]
                               or any(r['segment_id'] == segment['segment_id'] for r in shot.get('source_refs', []))]
        for segment in segments
    }
    return {
        "shot_count": len(shots),
        "missing_quoted_lines": missing_quotes(shots, chapter_text),
        "turn_count": turn_count,
        "turns_by_mode": by_mode,
        "spoken_chars": spoken,
        "verbatim_spoken_chars": verbatim,
        "verbatim_ratio": round(verbatim / spoken, 3) if spoken else None,
        "speakers": speakers,
        "segment_coverage": coverage,
        "skipped_segments": skipped,
    }


def missing_quotes(shots: list[dict], chapter_text: str) -> list[str]:
    joined = pc_text.quote_key("".join(turn["text"] for shot in shots for turn in shot["turns"]))
    return [quote for quote in pc_text.chapter_quotes(chapter_text) if pc_text.quote_key(quote) not in joined]
