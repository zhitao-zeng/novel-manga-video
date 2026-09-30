"""Shape mocked translations like the current dialogue-slot request contract."""
import re


def with_dialogue_slots(parts, shots):
    blocks = re.findall(r'(?ms)^\d+\. (.*?)(?=^\d+\. |\Z)', parts[0]['text'])
    return [shot + ''.join(' '+token for token in re.findall(r'<DIALOGUE_\d+>', block))
            for shot, block in zip(shots, blocks)]
