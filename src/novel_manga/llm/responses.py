"""Model content parsers; preserve each workflow's existing tolerance."""
import json
import re

def bible_object(value: str) -> dict:
    """Parse model JSON with bounded repairs for punctuation-only defects."""

    match = re.search(r"\{.*\}", value, re.S)
    if not match:
        raise ValueError("LLM did not return a JSON object")
    candidate = match.group(0)
    for _ in range(12):
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                raise ValueError("LLM JSON root must be an object")
            return data
        except json.JSONDecodeError as error:
            if error.msg == "Expecting ',' delimiter":
                previous = candidate[: error.pos].rstrip()
                following = candidate[error.pos :].lstrip()
                if previous and following and previous[-1] in '}\"]0123456789e' and following[0] in '{[\"':
                    candidate = candidate[: error.pos] + "," + candidate[error.pos :]
                    continue
            if error.msg == "Expecting property name enclosed in double quotes":
                previous = candidate[: error.pos].rstrip()
                if previous.endswith(","):
                    comma = candidate.rfind(",", 0, error.pos)
                    candidate = candidate[:comma] + candidate[comma + 1 :]
                    continue
            raise
    raise ValueError("LLM JSON exceeded the bounded punctuation repair budget")


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model must return one JSON object")
    return value


