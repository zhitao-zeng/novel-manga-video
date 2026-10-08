"""Recognize explicit assistant refusals, not negative task verdicts."""
import re


def refusal_reason(text):
    text = str(text or '')
    pattern = (r"\bI(?: (?:will not|won't|cannot|can't|refuse to|am not going to)|'m not going to) "
               r"(?:help|assist|continue|operate|produce|generate|comply|carry out)|"
               r'我(?:无法|不能|不会|拒绝)(?:协助|帮助|继续|执行|生成|制作)|拒绝继续操作')
    return text[:700] if re.search(pattern, text, re.I) else ''
