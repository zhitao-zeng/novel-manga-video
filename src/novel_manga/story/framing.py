"""Existing framing policy; never changes scene identities."""
from .actions import action_participants

def blocking_note(shot: dict) -> str:
    """Where each person in frame stands.  MiniMax's own guide asks for every subject's position in every shot, and
    the community's two-person staging - profile view, each on their own side - keeps two faces from blending into
    one.  From the stage's actions: the first actor at frame left, the person acted on at frame right facing them,
    everyone else behind them turned away and silent; a lone actor stands centre front.  Nothing for one person."""
    people = list(shot.get("characters") or [])
    if len(people) + len(shot.get('extras', [])) < 2:
        return ""
    subjects = set(people) | set(shot.get('extras', []))
    actions = [a for a in (shot.get("actions") or []) if a.get("actor") in subjects]
    if shot.get('actions') and not actions:
        return ''  # an unspecified/off-frame actor must not become another visible person
    if not actions and len(people) < 2:
        return ''
    # Whoever speaks on camera holds the frame.  The foreground used to go to actions[0]["actor"] -
    # whoever performs the physical action - and the speaker, having no action of their own, was sent
    # to `rest` and told 不开口 in the same stage whose 声音 line has them speaking.  ch12 (2026-09-22):
    # 15 of 43 spoken stages silenced their own speaker, and one gave the foreground to 银白色机甲
    # because the mech was the thing that moved.  The renderer does as it is told, and animates the
    # mouth it can see: the video judge found the wrong mouth in 7 shots of 16.
    speaking = [turn.get("speaker_name") for turn in (shot.get("turns") or [])
                if turn.get("delivery_mode") == "visible_dialogue" and turn.get("speaker_name") in subjects]
    actor = speaking[0] if speaking else (actions[0]["actor"] if actions else people[0])
    target = next((a.get("target") for a in actions if a.get("actor") == actor
                   and a.get("target") in subjects and a.get("target") != actor), None)
    if target is None and not actions:
        target = people[1]
    # only a pure bystander goes to the back: someone a later action reaches (the light that strikes <Subject 3>)
    # stays available for it
    involved = ({actor, target} | set(speaking) | {a.get("actor") for a in actions}
                | {a.get("target") for a in actions})
    rest = [n for n in people if n not in involved]
    relation = '两人侧面相对' if actor in people and target in people else '双方清楚分开'
    parts = ([f"{actor}在画面左侧前景，{target}在右侧前景，{relation}、各占一侧"] if target
             else [f"{actor}在前景居中"])
    if rest:
        parts.append(f"{'、'.join(rest)}只在后景侧身或背对镜头，不开口、不做主要动作")
    return "构图：" + "；".join(parts) + "。"



def visible_speaker_shots(base, turns_out, visible, position, *, split=True):
    """Keep the existing visible-speaker grouping and listener framing in reading order.

    `split=False` for a storyboard a person cut: the cuts are theirs, and re-cutting them is the one
    thing the authored path exists to prevent.  The risk the split guards against does not go away -
    two faces in one frame is where the renderer animates the wrong mouth - so it is reported instead.
    """
    normalized, warnings = [], []
    def framed(shot_base: dict, speaker: str) -> dict:
        """One visible speaker: only the speaker and the people the stage's actions involve stay in frame; the
        rest are listeners (back to camera or off frame).  Two faces in one frame is where the renderer animates
        the wrong mouth."""
        acting = action_participants(shot_base["actions"])
        keep = [c for c in shot_base["characters"] if c == speaker or c in acting]
        listeners = [c for c in shot_base["characters"] if c not in keep]
        if listeners:
            warnings.append(f"{position}: {speaker} 说话，{listeners} 转为听者（背影或画外）")
        return {**shot_base, "characters": keep or shot_base["characters"], "listeners": listeners if keep else []}

    distinct_visible = list(dict.fromkeys(visible))
    if len(distinct_visible) <= 1:
        normalized.append({**(framed(base, distinct_visible[0]) if distinct_visible else base), "turns": turns_out})
        return normalized, warnings
    if not split:
        # Chapter 10 of the pilot: four lines of one exchange in one authored 11-second shot, whose
        # camera column says the blocking replaces shot/reverse-shot.  Splitting made four shots that
        # each kept the whole 11 seconds, and the episode went from 95 seconds to 232.
        warnings.append(f"{position}: {len(distinct_visible)} 个可见说话人在同一镜里（作者的分镜，不拆）；"
                        "渲染器可能把口型安错人，出片后重点看这一镜")
        return [{**base, "turns": turns_out}], warnings
    warnings.append(f"{position}: {len(distinct_visible)} visible speakers; split into consecutive shots")
    groups: list[list[dict]] = []
    current_speaker = None
    for turn in turns_out:
        if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"] != current_speaker:
            if groups and current_speaker is None:
                groups[-1].append(turn)
                current_speaker = turn["speaker_name"]
                continue
            groups.append([turn])
            current_speaker = turn["speaker_name"]
            continue
        if not groups:
            groups.append([])
        groups[-1].append(turn)
    for group in groups:
        if group:
            speaker = next((t["speaker_name"] for t in group if t["delivery_mode"] == "visible_dialogue" and t["speaker_name"]), "")
            normalized.append({**(framed(base, speaker) if speaker else base), "turns": group})

    return normalized, warnings
