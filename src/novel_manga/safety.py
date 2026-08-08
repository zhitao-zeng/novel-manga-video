from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyFinding:
    category: str
    matched: str


_BLOCK_PATTERNS = {
    "sexual": re.compile(r"(?:强奸|乱伦|性交|裸体性爱|未成年.{0,8}性)", re.I),
    "political": re.compile(r"(?:推翻政府|暗杀(?:总统|主席|国家领导人)|分裂国家)", re.I),
    "graphic_violence": re.compile(r"(?:开膛破肚|肢解尸体|血肉横飞|剥皮|虐杀)", re.I),
}


def scan_source(text: str) -> list[SafetyFinding]:
    findings: list[SafetyFinding] = []
    for category, pattern in _BLOCK_PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(SafetyFinding(category=category, matched=match.group(0)))
    return findings


def safe_visual_prompt(prompt: str) -> str:
    replacements = {
        "鲜血": "紧张的红色光影",
        "血迹": "凌乱痕迹",
        "尸体": "倒地的人物剪影",
        "杀死": "击败",
        "砍下": "制服",
        "裸体": "穿着完整",
    }
    for source, target in replacements.items():
        prompt = prompt.replace(source, target)
    return prompt + "，内容健康克制，无血腥、无裸露、无政治符号"
