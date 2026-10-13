"""A current H3 clip and minimal host context for submission boundary tests."""
import json
from types import SimpleNamespace
from PIL import Image
from support.render_context import uninitialized_runner

PROMPT = "【生成目标】一段 15 秒片段。\n【阶段】1. 他推门。"


def h3_clip(**extra):
    return {"clip_id": "clip_01", "kind": "video", "request_seconds": 15,
            "prompt": PROMPT, "references": [], "lines": [], **extra}


def runner_for(tmp_path, monkeypatch, clip, *, plan=None):
    directory = tmp_path / "nov" / "nov_1"
    directory.mkdir(parents=True, exist_ok=True)
    plan = plan or {"limits": {"max_clip_seconds": 15}, "clips": [clip]}
    (directory / "clip_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    for ref in clip.get("references") or []:            # a referenced card exists on disk
        path = directory.parent / ref["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (16, 16), 'white').save(path)
    r = uninitialized_runner()
    r.context.episode_dir, r.context.novel_dir = directory, directory.parent
    r.context.work = directory / "work"
    r.context.clip_plan, r.context.script, r.context.feedback = plan, {"shots": []}, {}
    r.context.options.cache_only, r.context.options.max_attempts = False, 1
    r.context.settings = SimpleNamespace(local_h3_base_url="http://h3.local")
    monkeypatch.setattr(r, "prescreens", lambda c: False)
    return r


ENGLISH_BODY = ("subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\n\nsummary:\n\n"
                "detailed_description:\n[Shot 1] <Subject 1> (S1) says <d>[Chinese] 你好</d> and pushes the door.\n\n"
                "overall_soundscape:\nroom tone")


def _bound(**extra):
    return h3_clip(prompt_h3=ENGLISH_BODY, scene_ids=["sc1"], shot_timing=[{"seconds": 4}],
                   references=[{"role": "character", "name": "甲", "path": "c1/turnaround.jpeg"}],
                   dialogue_bindings=[{"stage": 1, "speaker_name": "甲",
                                       "delivery_mode": "visible_dialogue", "text": "你好"}], **extra)
