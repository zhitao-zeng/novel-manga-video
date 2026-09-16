"""Original repair schema, including optional reframing fields."""
from __future__ import annotations

from novel_manga.story.fields import cast_field, actions_field, extras_field

def schema_for(names: list[str], indexes: list[int], *, reframe=False, bible=None) -> dict:
    schema = {"type": "object", "additionalProperties": False, "required": ["stages"], "properties": {"stages": {
        "type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
                                                                  "required": ["origin_index", "in_frame", "actions", "extras", "event"],
                                                                  "properties": {
                                                                      "origin_index": {"type": "integer", "enum": indexes},
                                                                      "in_frame": cast_field(names),
                                                                      "actions": actions_field(),
                                                                      "extras": extras_field(),
                                                                      "event": {"type": "string"}}}}}}


    bible = bible or {}
    if reframe:
        schema["properties"]["stages"]["items"]["properties"].update({
            "visual_prompt": {"type": "string", "maxLength": 200}, "camera": {"type": "string", "maxLength": 60},
            "shot_scale": {"type": "string", "enum": ["远景", "全景", "中景", "近景", "特写"]},
            "end_state": {"type": "string", "maxLength": 200},
            "location": {"type": "string", "enum": list(dict.fromkeys(
                str(loc).split('：', 1)[0] for loc in bible.get('locations', [])))},
            "speakers": {"type": "array", **({"maxItems": 0} if not names else {}),
                "items": {"type": "object", "additionalProperties": False,
                "required": ['turn_index', 'speaker_name'], 'properties': {'turn_index': {'type': 'integer', 'minimum': 1},
                'speaker_name': {'type': 'string', **({'enum': names} if names else {})}}}}})
        if not bible.get('locations'):
            schema['properties']['stages']['items']['properties'].pop('location')
        schema["properties"]["stages"]["items"]["required"].extend(["visual_prompt", "camera", "shot_scale", "end_state", "speakers"])
    return schema
