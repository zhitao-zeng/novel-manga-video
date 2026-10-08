"""Contiguous source evidence, shared without review-module dependency cycles."""
import re

def excerpts(text: str, name: str, limit: int = 12) -> str:
    """Keep the surrounding description and reply, not just the sentence naming somebody."""
    sentences = re.split(r"(?<=[。！？])", text)
    hits = [i for i, sentence in enumerate(sentences) if name in sentence][:limit]
    selected = sorted({j for i in hits for j in range(max(0, i - 2), min(len(sentences), i + 3))})
    passages, previous = [], -1
    for index in selected:
        if previous >= 0 and index > previous + 1:
            passages.append("\n[另一处原文]\n")
        passages.append(sentences[index])
        previous = index
    return "".join(passages)[:4000]
