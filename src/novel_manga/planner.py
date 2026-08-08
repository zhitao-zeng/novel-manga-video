from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import Character, Episode, EpisodePlan, NovelDocument, ScriptTurn, Shot, StoryBible
from .safety import safe_visual_prompt


STYLE = (
    "精致国漫动态漫画，二维赛璐璐手绘，清晰墨线，柔和电影光影，"
    "人物五官稳定，服饰连续，竖屏中近景构图，禁止真人照片、3D和欧美卡通混入"
)

ValidatedT = TypeVar("ValidatedT")


def _validation_feedback(error: ValueError) -> list[dict[str, object]]:
    if isinstance(error, ValidationError):
        return [
            {
                "location": [str(item) for item in row["loc"]],
                "type": row["type"],
                "message": row["msg"],
            }
            for row in error.errors(include_url=False)
        ]
    return [{"type": type(error).__name__, "message": str(error)[:3000]}]


def _bounded_validate(
    operation: str,
    max_revisions: int,
    request: Callable[[dict | None], dict],
    validate: Callable[[dict], ValidatedT],
) -> ValidatedT:
    """Ask a planner to repair only invalid structured output, with a hard limit."""

    repair: dict | None = None
    last_error: ValueError | None = None
    for revision in range(max_revisions + 1):
        data: dict | None = None
        try:
            data = request(repair)
            return validate(data)
        except (ValidationError, ValueError) as error:
            last_error = error
            if revision >= max_revisions:
                break
            repair = {
                "revision": revision + 1,
                "previous_response": data,
                "validation_errors": _validation_feedback(error),
            }
    assert last_error is not None
    details = json.dumps(_validation_feedback(last_error), ensure_ascii=False)
    raise ValueError(
        f"planner operation {operation} remained invalid after "
        f"{max_revisions + 1} attempt(s): {details}"
    ) from last_error


def _validate_story_bible(data: dict, novel: NovelDocument) -> StoryBible:
    bible = StoryBible.model_validate(data)
    issues: list[dict[str, object]] = []
    if re.sub(r"\s+", "", bible.novel_title) != re.sub(r"\s+", "", novel.title):
        issues.append({"field": "novel_title", "message": "must equal the requested novel title"})
    if not bible.characters:
        issues.append({"field": "characters", "message": "at least one reusable character is required"})
    if not bible.locations:
        issues.append({"field": "locations", "message": "at least one reusable location is required"})
    names = [character.name.strip() for character in bible.characters]
    if len(set(names)) != len(names):
        issues.append({"field": "characters", "message": "character names must be unique"})
    for index, character in enumerate(bible.characters):
        if not character.name.strip() or not character.appearance.strip() or not character.wardrobe.strip():
            issues.append({
                "field": f"characters.{index}",
                "message": "name, appearance, and wardrobe must be non-empty",
            })
    if issues:
        raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
    return bible.model_copy(
        update={"style_fingerprint": _fingerprint(novel.title, bible.visual_style, bible.characters)}
    )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    merged: list[str] = []
    for part in parts:
        clean = re.sub(r"\s+", "", part)
        if not clean:
            continue
        if re.fullmatch(r"[”’」』】）》]+", clean) and merged:
            merged[-1] += clean
        else:
            merged.append(clean)
    return merged


def _compact_excerpt(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    third = limit // 3
    middle = len(text) // 2
    return text[:third] + "\n[中段抽样]\n" + text[middle - third // 2:middle + third // 2] + "\n[结尾]\n" + text[-third:]


def _short_beats(sentences: list[str], limit: int = 80) -> list[str]:
    beats: list[str] = []
    for sentence in sentences:
        if len(sentence) <= limit:
            beats.append(sentence)
            continue
        clauses = re.findall(r".+?[，、：,]|.+$", sentence)
        current = ""
        for clause in clauses:
            if current and len(current) + len(clause) > limit:
                beats.append(current)
                current = ""
            if len(clause) <= limit:
                current += clause
            else:
                if current:
                    beats.append(current)
                    current = ""
                beats.extend(clause[index:index + limit] for index in range(0, len(clause), limit))
        if current:
            beats.append(current)
    return beats


def _fingerprint(title: str, style: str, characters: list[Character]) -> str:
    payload = title + style + "|".join(f"{c.name}:{c.appearance}:{c.wardrobe}" for c in characters)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _script_turns(sentence: str, character_names: list[str]) -> list[ScriptTurn]:
    """Extract explicit quoted speech; keep everything else as closed-mouth narration."""
    dialogue = re.search(r"[“\"](.+?)[”\"]", sentence)
    if not dialogue:
        return [ScriptTurn(text=sentence[:80], source_quote=sentence[:120])]
    prefix = sentence[: dialogue.start()]
    candidates = [
        name
        for name in character_names
        if re.search(rf"{re.escape(name)}.{{0,16}}(?:说|问|答|喊)", prefix)
    ]
    if not candidates:
        return [ScriptTurn(text=sentence[:80], source_quote=sentence[:120])]
    speaker = max(candidates, key=prefix.rfind)
    line = dialogue.group(1)
    narration_prefix = prefix.rstrip("：“\"")
    turns: list[ScriptTurn] = []
    if narration_prefix:
        turns.append(ScriptTurn(text=narration_prefix[:80], source_quote=sentence[:120]))
    turns.append(
        ScriptTurn(
            role=speaker,
            speaker_name=speaker,
            text=line[:500],
            speaking=True,
            emotion="符合原文语气",
            source_quote=sentence[:120],
        )
    )
    return turns


class Planner(ABC):
    @abstractmethod
    def build_bible(self, novel: NovelDocument) -> StoryBible: ...

    @abstractmethod
    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan: ...


class DeterministicPlanner(Planner):
    def build_bible(self, novel: NovelDocument) -> StoryBible:
        names = re.findall(
            r"(?:^|[，。！？：“”\n])([\u4e00-\u9fff]{2,3})(?=(?:低声|轻声|高声|冷冷地)?(?:说|问|答|喊)|看向|走出|走进|推开|发现|把|从)",
            novel.text,
        )
        stopwords = {"低声", "忽然", "就在", "这时", "只说", "故事", "第一章", "第二章"}
        names = [name for name in names if name not in stopwords]
        ordered = list(dict.fromkeys(names))[:4]
        characters = [
            Character(
                name=name,
                role="主要人物" if i < 2 else "配角",
                appearance=f"{name}，黑发，轮廓清晰，神态具有辨识度",
                wardrobe="与故事时代匹配的固定主色服装",
            )
            for i, name in enumerate(ordered)
        ]
        if not characters:
            characters = [Character(
                name="主角",
                role="主角",
                appearance="黑发青年，清晰稳定的东亚面孔，眼神坚定",
                wardrobe="深蓝与银灰配色的固定服装",
            )]
        location_matches = re.findall(
            r"[\u4e00-\u9fff]{0,6}(?:书店|办公室|广场|庭院|山谷|街道|学校|教室|医院|客厅|卧室|地下室|门外)",
            novel.text,
        )
        cleaned_locations = []
        for match in location_matches:
            clean = re.sub(r"^.*(?:推开|进入|走进|来到|离开|回到|站在|看向)", "", match.strip())
            if clean:
                cleaned_locations.append(clean)
        locations = list(dict.fromkeys(cleaned_locations))[:8]
        if not locations:
            locations = ["与原文一致的主要场景"]
        fingerprint = _fingerprint(novel.title, STYLE, characters)
        return StoryBible(
            novel_title=novel.title,
            genre="小说改编漫剧",
            visual_style=STYLE,
            palette="青蓝与暖金平衡，冲突场景使用克制的红色点缀",
            characters=characters,
            locations=locations,
            continuity_rules=[
                "同一角色的性别、年龄、脸型、发型和服装主色不得变化",
                "同一地点的空间结构、时间、天气与关键物品保持连续",
                "不得新增改变人物关系、关键事件、因果或结局的情节",
            ],
            style_fingerprint=fingerprint,
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        episode_text = episode.source_text.strip()
        if episode_text.startswith(episode.source_title):
            episode_text = episode_text[len(episode.source_title):].lstrip()
        sentences = _short_beats(_sentences(episode_text))
        if not sentences:
            sentences = [episode.source_text.strip()]
        shots: list[Shot] = []
        for index, sentence in enumerate(sentences, 1):
            narration = sentence
            source_quote = sentence
            character_names = [c.name for c in bible.characters if c.name in sentence]
            location = next(
                (candidate for candidate in bible.locations if candidate in sentence),
                bible.locations[0] if bible.locations else "原文当前场景",
            )
            visual = safe_visual_prompt(
                f"{bible.visual_style}。{bible.palette}。剧情：{sentence}。"
                f"角色设定：{'；'.join(c.name + c.appearance + c.wardrobe for c in bible.characters[:3])}"
            )
            shots.append(Shot(
                index=index,
                narration=narration,
                subtitle=narration,
                visual_prompt=visual,
                motion_prompt="轻微推镜，人物自然眨眼与细微表情变化，保持脸部和服装稳定",
                characters=character_names,
                location=location,
                source_quote=source_quote,
                turns=_script_turns(sentence, [character.name for character in bible.characters]),
            ))
        title_hint = episode.source_title if episode.source_title else shots[0].subtitle[:12]
        return EpisodePlan(
            video_title=title_hint[:30],
            hook=shots[0].narration,
            summary="".join(shot.narration for shot in shots)[:240],
            shots=shots,
        )


class OpenAICompatiblePlanner(Planner):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout)

    def _json(self, system: str, user: str, repair: dict | None = None) -> dict:
        base = str(self.settings.llm_base_url).rstrip("/")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if repair:
            if repair.get("previous_response") is not None:
                messages.append({
                    "role": "assistant",
                    "content": json.dumps(repair["previous_response"], ensure_ascii=False),
                })
            messages.append({
                "role": "user",
                "content": (
                    "上一次 JSON 未通过确定性校验。只修复列出的错误，继续忠于输入原文，"
                    "不要解释、不要输出 Markdown。校验反馈："
                    + json.dumps(repair["validation_errors"], ensure_ascii=False)
                ),
            })
        response = self.client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            json={
                "model": self.settings.llm_model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": messages,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("LLM did not return a JSON object")
        return json.loads(match.group(0))

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        schema = StoryBible.model_json_schema()
        system = (
            "你是漫剧总美术和小说事实核验员。只提取原文可支持的信息；外貌未写明时可做克制设计。"
            "所有角色必须是健康、非色情、非血腥的统一国漫画风。严格输出 JSON。"
        )
        user = (
            f"小说名：{novel.title}\n文本：{_compact_excerpt(novel.text)}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}\n"
            f"visual_style 必须包含：{STYLE}。style_fingerprint 暂填空字符串。"
        )
        return _bounded_validate(
            "build_bible",
            self.settings.planner_max_revisions,
            lambda repair: self._json(system, user, repair),
            lambda data: _validate_story_bible(data, novel),
        )

    @staticmethod
    def _canonical_character_name(name: str, canonical_names: list[str]) -> str:
        normalized = re.sub(r"\s+", "", name)
        if name in canonical_names:
            return name
        emitted_aliases = {
            re.sub(r"\s+", "", item)
            for item in re.split(r"[/／|、]", name)
            if item.strip()
        }
        matches = []
        for canonical in canonical_names:
            normalized_canonical = re.sub(r"\s+", "", canonical)
            aliases = [
                re.sub(r"\s+", "", item)
                for item in re.split(r"[/／|、]", canonical)
                if item.strip()
            ]
            if (
                normalized in aliases
                or normalized in normalized_canonical
                or normalized_canonical in emitted_aliases
            ):
                matches.append(canonical)
        return matches[0] if len(matches) == 1 else name

    def _canonicalize_characters(self, plan: EpisodePlan, bible: StoryBible) -> EpisodePlan:
        canonical_names = [character.name for character in bible.characters]
        shots = []
        for shot in plan.shots:
            turns = []
            for turn in shot.turns:
                if turn.speaking:
                    speaker = self._canonical_character_name(turn.speaker_name, canonical_names)
                    turns.append(turn.model_copy(update={"speaker_name": speaker, "role": speaker}))
                else:
                    turns.append(turn)
            characters = [
                self._canonical_character_name(character, canonical_names)
                for character in shot.characters
            ]
            shots.append(shot.model_copy(update={"characters": characters, "turns": turns}))
        return plan.model_copy(update={"shots": shots})

    def _ground_quotes(self, plan: EpisodePlan, source: str, bible: StoryBible) -> EpisodePlan:
        plan = self._canonicalize_characters(plan, bible)
        normalized_source = re.sub(r"\s+", "", source)
        source_sentences = _sentences(source)
        grounded: list[Shot] = []
        for shot in plan.shots:
            quote = re.sub(r"\s+", "", shot.source_quote)
            if quote not in normalized_source:
                quote = max(
                    source_sentences,
                    key=lambda item: SequenceMatcher(None, item, shot.narration).ratio(),
                    default=source[:80],
                )[:110]
            grounded_turns = []
            for turn in shot.turns:
                turn_quote = re.sub(r"\s+", "", turn.source_quote)
                grounded_turns.append(turn.model_copy(update={
                    "source_quote": turn.source_quote
                    if turn_quote and turn_quote in normalized_source
                    else quote,
                }))
            grounded.append(shot.model_copy(update={
                "source_quote": quote,
                "visual_prompt": safe_visual_prompt(shot.visual_prompt),
                "turns": grounded_turns,
            }))
        return plan.model_copy(update={"shots": grounded})

    def _validate_episode_data(
        self,
        data: dict,
        episode: Episode,
        bible: StoryBible,
    ) -> EpisodePlan:
        plan = self._canonicalize_characters(EpisodePlan.model_validate(data), bible)
        normalized_source = re.sub(r"\s+", "", episode.source_text)
        canonical_names = {character.name for character in bible.characters}
        canonical_locations = set(bible.locations)
        issues: list[dict[str, object]] = []
        for shot in plan.shots:
            shot_quote = re.sub(r"\s+", "", shot.source_quote)
            if not shot_quote or shot_quote not in normalized_source:
                issues.append({
                    "field": f"shots.{shot.index}.source_quote",
                    "message": "must be an exact excerpt of this episode source",
                })
            unknown_characters = sorted(set(shot.characters) - canonical_names)
            if unknown_characters:
                issues.append({
                    "field": f"shots.{shot.index}.characters",
                    "message": "must use StoryBible character names",
                    "unknown": unknown_characters,
                })
            if shot.location and shot.location not in canonical_locations:
                issues.append({
                    "field": f"shots.{shot.index}.location",
                    "message": "must use a StoryBible location",
                    "unknown": shot.location,
                })
            for turn_index, turn in enumerate(shot.turns):
                turn_quote = re.sub(r"\s+", "", turn.source_quote)
                if not turn_quote or turn_quote not in normalized_source:
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.source_quote",
                        "message": "must be an exact excerpt of this episode source",
                    })
                if turn.speaking and turn.speaker_name not in canonical_names:
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.speaker_name",
                        "message": "visible speaker must use a StoryBible character name",
                        "unknown": turn.speaker_name,
                    })
                if turn.speaking and re.sub(r"\s+", "", turn.text) not in turn_quote:
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.text",
                        "message": "visible dialogue must occur verbatim inside source_quote",
                    })
        if issues:
            raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
        return self._ground_quotes(plan, episode.source_text, bible)

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        schema = EpisodePlan.model_json_schema()
        source_chars = len(re.sub(r"\s+", "", episode.source_text))
        if source_chars <= 1200:
            size_guidance = "全片旁白与对白合计450-750个汉字、8-14个镜头"
        elif source_chars <= 3000:
            size_guidance = "全片旁白与对白合计700-1100个汉字、16-24个镜头"
        else:
            size_guidance = "全片旁白与对白合计900-1400个汉字、24-36个镜头"
        system = (
            "你是小说改编漫剧编剧。忠于原文人物关系、关键事件、顺序、因果和结局；不得新增核心情节。"
            f"前4秒片头后立即进入冲突或悬念。原文有效字数约{source_chars}，{size_guidance}；"
            "不得为凑镜头或字数重复情节、虚构事件或拆碎同一句话。"
            "每个镜头设置 turns：旁白 role=narrator、speaking=false；人物对白 role 和 speaker_name 均使用角色原名、"
            "speaking=true，并逐字保留对白。一个 turn 只能有一个可见说话人；字幕严格等于 turn.text。"
            "每个镜头总文本15-80个汉字，最多两行分页；"
            "覆盖本章开端、发展、高潮和结尾。source_quote 必须逐字摘自本章。画面健康克制，无色情、政治和血腥。"
            "严格输出 JSON。"
        )
        user = (
            f"小说：{novel.title}\n本集：{episode.source_title}\n故事圣经：{bible.model_dump_json()}\n"
            f"原文：{episode.source_text}\nJSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "plan_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(system, user, repair),
            lambda data: self._validate_episode_data(data, episode, bible),
        )


class CommandPlanner(Planner):
    """Model-neutral planner adapter using a small JSON file contract.

    The configured command receives ``--operation``, ``--input`` and ``--output``.
    It can call any local model, hosted model, or orchestration service and must write
    JSON matching the requested schema.
    """

    def __init__(self, settings: Settings):
        if not settings.planner_command:
            raise ValueError("planner command is missing")
        self.command = shlex.split(settings.planner_command)
        self.max_revisions = settings.planner_max_revisions

    def _invoke(self, operation: str, payload: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="novel-planner-") as directory:
            root = Path(directory)
            request = root / "request.json"
            response = root / "response.json"
            request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            subprocess.run(
                self.command
                + ["--operation", operation, "--input", str(request), "--output", str(response)],
                check=True,
                capture_output=True,
                text=True,
            )
            if not response.is_file():
                raise RuntimeError("planner command did not create its JSON output")
            return json.loads(response.read_text(encoding="utf-8"))

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        payload = {
            "contract": "novel-manga-planner/v2",
            "novel": {
                "novel_id": novel.novel_id,
                "title": novel.title,
                "text": _compact_excerpt(novel.text),
            },
            "schema": StoryBible.model_json_schema(),
            "requirements": {"style": STYLE, "source_faithful": True},
        }
        return _bounded_validate(
            "build_bible",
            self.max_revisions,
            lambda repair: self._invoke(
                "build_bible",
                {**payload, **({"repair": repair} if repair else {})},
            ),
            lambda data: _validate_story_bible(data, novel),
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        payload = {
            "contract": "novel-manga-planner/v2",
            "novel_id": novel.novel_id,
            "episode": episode.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "schema": EpisodePlan.model_json_schema(),
            "requirements": {
                "one_visible_speaker_per_turn": True,
                "exact_turn_text": True,
                "source_quotes_required": True,
                "first_story_beat_within_seconds": 10,
            },
        }
        normalizer = object.__new__(OpenAICompatiblePlanner)
        return _bounded_validate(
            "plan_episode",
            self.max_revisions,
            lambda repair: self._invoke(
                "plan_episode",
                {**payload, **({"repair": repair} if repair else {})},
            ),
            lambda data: normalizer._validate_episode_data(data, episode, bible),
        )
