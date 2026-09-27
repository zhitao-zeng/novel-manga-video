"""Positive speech corrections shared by retries and the episode review report."""
from __future__ import annotations


def correction(clip: dict, analysis: dict) -> str:
    if analysis.get('passed') or not {'excess_unplanned_speech', 'unscripted_speech'}.intersection(analysis.get('issues', [])):
        return ''
    if clip.get('audio_delivery') == 'postmix':
        return '本镜是无声表演，人声在后期加入。画内人物嘴部自然闭合，仅保留原分镜动作与环境声。'
    if clip.get('spoken_text') or any(row.get('text') for row in clip.get('lines', [])):
        return ('本镜开始后直接进入已经绑定的台词，发声内容仅为这些台词，按原顺序说一遍。'
                '说话前及说完后人物闭嘴，仅保留场景环境声与原分镜动作；画面与剧情保持原分镜。')
    return '本镜人物不说话，嘴部保持自然闭合，仅有原分镜的动作和环境声。'


def merge_note(previous: str, addition: str) -> str:
    previous = str(previous or '').strip()
    return previous if addition in previous else '\n'.join(filter(None, [previous, addition]))
