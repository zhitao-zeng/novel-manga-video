"""Validate a Seedance request edit without imposing the H3 request grammar."""
import re


def validate(clip, prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('SD request is empty')
    old = clip['prompt']
    # The compiler owns reference definitions, style, total duration and audio.
    # The repairer can clarify visible action inside existing stages only.
    def locked(text):
        stages, fixed, audio = [], [], []
        for line in text.splitlines():
            header = re.match(r'【阶段[^】]*】', line)
            if not header:
                fixed.append(line)
                continue
            stages.append(header.group())
            sound = re.search(r'声音：.*?(?=结束时：|$)', line)
            audio.append(sound.group() if sound else '')
        return (stages, fixed, audio, re.findall(r'\{[^{}]*\}', text),
                re.findall(r'@(?:图片|音频)\d+', text))
    if not locked(old)[0]:
        raise ValueError('SD request has no compiler stage boundaries')
    if locked(old) != locked(prompt):
        raise ValueError('SD edit changed protected stages, reference mappings, dialogue or sound bindings')
    if any(token in prompt for token in ('subject_definitions:', '<Subject ', '<Picture ', '<Audio ')):
        raise ValueError('SD request must not contain H3-only request sections or tags')
