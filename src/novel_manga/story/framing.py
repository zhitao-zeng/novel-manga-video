"""Dialogue grouping that preserves authored visibility and camera decisions."""
def blocking_note(shot: dict) -> str:
    """Carry visibility into a stage without inventing positions or orientations."""
    names = shot.get('in_frame', shot.get('characters') or [])
    return '入镜：' + ('、'.join(names) or '无具名人物') + '。'



def visible_speaker_shots(base, turns_out, visible, position, *, split=True):
    """Keep the existing visible-speaker grouping and listener framing in reading order.

    `split=False` for a storyboard a person cut: the cuts are theirs, and re-cutting them is the one
    thing the authored path exists to prevent.  The risk the split guards against does not go away -
    two faces in one frame is where the renderer animates the wrong mouth - so it is reported instead.
    """
    normalized, warnings = [], []
    def framed(shot_base: dict, speaker: str) -> dict:
        """Listening describes a role, not a camera pose or an absence."""
        listeners = [name for name in shot_base['characters'] if name != speaker]
        return {**shot_base, 'listeners': listeners}

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
            piece = dict(base)
            if normalized:
                piece['actions'] = []
                piece['visual_prompt'] = str(base.get('end_state') or '')
                piece['motion_prompt'] = ''
            normalized.append({**(framed(piece, speaker) if speaker else piece), "turns": group})

    return normalized, warnings
