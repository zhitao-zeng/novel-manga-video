"""Existing framing policy; never changes scene identities."""

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
    actor = actions[0]["actor"] if actions else people[0]
    target = next((a.get("target") for a in actions if a.get("actor") == actor
                   and a.get("target") in subjects and a.get("target") != actor), None)
    if target is None and not actions:
        target = people[1]
    # only a pure bystander goes to the back: someone a later action reaches (the light that strikes <Subject 3>)
    # stays available for it
    involved = {actor, target} | {a.get("actor") for a in actions} | {a.get("target") for a in actions}
    rest = [n for n in people if n not in involved]
    relation = '两人侧面相对' if actor in people and target in people else '双方清楚分开'
    parts = ([f"{actor}在画面左侧前景，{target}在右侧前景，{relation}、各占一侧"] if target
             else [f"{actor}在前景居中"])
    if rest:
        parts.append(f"{'、'.join(rest)}只在后景侧身或背对镜头，不开口、不做主要动作")
    return "构图：" + "；".join(parts) + "。"
