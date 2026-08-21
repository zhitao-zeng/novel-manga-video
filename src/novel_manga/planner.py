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
from .creative_direction import (
    SHORT_DRAMA_PROFILE,
    apply_creative_direction,
    creative_prompt_brief,
)
from .models import (
    CameraBeat,
    CameraPlan,
    Character,
    ChapterDiagnosis,
    Episode,
    EpisodePlanningBundle,
    EpisodePlan,
    MotionBeat,
    NovelDocument,
    PerformancePlan,
    ScriptTurn,
    ScriptQualityReport,
    ScriptExpansion,
    SeriesState,
    ShowrunnerPlan,
    Shot,
    StoryBible,
    TurnDerivation,
)
from .safety import safe_visual_prompt
from .script_planning import (
    bind_deterministic_events,
    deterministic_chapter_diagnosis,
    deterministic_series_state,
    evaluate_script_quality,
    repair_machine_draft,
    script_policy,
    normalize_chronological_plan,
    source_evidence_units,
    validate_chapter_diagnosis,
    validate_series_state,
)
from .util import atomic_write_json


STYLE = (
    "精致国漫动态漫画，二维赛璐璐手绘，清晰墨线，柔和电影光影，"
    "人物五官稳定，服饰连续，竖屏中近景构图，禁止真人照片、3D和欧美卡通混入"
)

DIAGNOSIS_TOKEN_BUDGET = 6000
SCRIPT_TOKEN_BUDGET = 32000
REVIEW_TOKEN_BUDGET = 4000
SHOWRUNNER_TOKEN_BUDGET = 6000
SERIES_STATE_TOKEN_BUDGET = 5000
SCRIPT_EXPANSION_TOKEN_BUDGET = 6000

ValidatedT = TypeVar("ValidatedT")


def _loads_json_object(value: str) -> dict:
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

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        """Backward-compatible audited wrapper for deterministic/command planners."""

        diagnosis = deterministic_chapter_diagnosis(episode)
        plan = bind_deterministic_events(self.plan_episode(novel, episode, bible), diagnosis)
        profile = getattr(self, "creative_profile", "faithful-chronological-v1")
        plan = apply_creative_direction(
            plan,
            diagnosis,
            bible,
            profile=profile,
        )
        plan = normalize_chronological_plan(plan, diagnosis, episode)
        report = evaluate_script_quality(
            plan, diagnosis, episode, previous_state=previous_state
        )
        if not report.passed:
            raise ValueError(
                "script quality gate failed: "
                + json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
            )
        state = deterministic_series_state(episode, diagnosis, previous_state)
        return EpisodePlanningBundle(
            diagnosis=diagnosis,
            plan=plan,
            quality_report=report,
            updated_series_state=state,
        )


class DeterministicPlanner(Planner):
    def __init__(self, settings: Settings | None = None):
        self.creative_profile = (
            settings.creative_profile if settings is not None else "faithful-chronological-v1"
        )

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
            turns = _script_turns(sentence, [character.name for character in bible.characters])
            has_dialogue = any(turn.speaking for turn in turns)
            performance_plan = PerformancePlan(
                objective=f"用连续动作讲清“{sentence[:60]}”，不是动态照片",
                start_state="人物处于事件开始前一瞬，视线、手部和身体重心仍有动作空间",
                motion_beats=[
                    MotionBeat(
                        phase="opening",
                        trigger="事件或台词开始",
                        action="眼睛先移动，头部随后转向目标，肩膀和上身稍后跟随",
                        reaction="身体重心随观察或说话方向发生变化",
                    ),
                    MotionBeat(
                        phase="development",
                        trigger="人物确认当前事件",
                        action=f"完成与剧情直接相关的动作：{sentence[:100]}",
                        reaction="手部动作带动身体响应，道具、头发和衣物体现惯性",
                    ),
                    MotionBeat(
                        phase="resolution",
                        trigger="本镜信息表达完成",
                        action="动作减速并停在能承接下一镜的位置",
                        reaction="呼吸和次级运动自然收束",
                    ),
                ],
                end_state="人物完成本镜动作，事件结果和最终表情清楚可读",
            )
            camera_plan = CameraPlan(
                mode="locked",
                motivation="默认由人物表演承担画面动态，稳定人物和场景空间关系",
                action_axis=f"{location}首次建立的人物视线或运动轴同侧",
                screen_direction="保持人物左右位置、视线和运动方向连续",
                start_position="竖屏中近景，画面包含前景、中景和远景层次",
                camera_beats=[
                    CameraBeat(
                        phase="opening",
                        trajectory="锁定机位，摄影机全程保持静止",
                        framing="通过人物视线、手势、姿态和画内走位保持动态",
                        parallax=f"不制造摄影机视差，{location}前中远景保持固定",
                    ),
                    CameraBeat(
                        phase="resolution",
                        trajectory="继续锁定机位，让动作结果和表情停留一拍",
                        framing="不推拉、不横移、不环绕，读清动作结果",
                        parallax="背景结构和人物屏幕位置保持稳定",
                    ),
                ],
                end_position="与起始位置相同的稳定机位",
            )
            shots.append(Shot(
                index=index,
                narration=narration,
                subtitle=narration,
                visual_prompt=visual,
                motion_prompt="固定机位，人物通过视线、手势和身体重心完成有因果的表演，保持脸部和服装稳定",
                characters=character_names,
                location=location,
                source_quote=source_quote,
                turns=turns,
                performance_plan=performance_plan,
                camera_plan=camera_plan,
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

    def _json(
        self,
        system: str,
        user: str,
        repair: dict | None = None,
        *,
        token_budget: int | None = None,
    ) -> dict:
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
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "max_tokens": min(
                self.settings.llm_max_tokens,
                token_budget or self.settings.llm_max_tokens,
            ),
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        if self.settings.llm_disable_thinking:
            # vLLM/Qwen accepts this OpenAI-compatible extension.  Keep it
            # opt-in so hosted OpenAI-compatible providers are unaffected.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        response = self.client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            json=payload,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _loads_json_object(content)

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        schema = StoryBible.model_json_schema()
        system = (
            "你是漫剧总美术和小说事实核验员。只提取原文可支持的信息；外貌未写明时可做克制设计。"
            "每名主要角色还要建立可跨集复用的选角档案：visual_archetype写社会与戏剧类型，"
            "face_anchors写3-5个不可漂移的五官锚点，silhouette、hair、palette和base_costume必须彼此可区分，"
            "signature_prop只填写原文支持或不改变剧情的识别物，expression_profile描述表情幅度，"
            "motion_signature描述角色惯用姿态和动作节奏，voice_profile_id填写稳定的声音角色标识。"
            "场景locations应是可生成空场资产的地点名，不把人物动作写进地点。"
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
            lambda repair: self._json(
                system, user, repair, token_budget=DIAGNOSIS_TOKEN_BUDGET
            ),
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

    @staticmethod
    def _is_anonymous_crowd(name: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:路人|族人|人群|围观者|旁人|少年|少女|弟子|群众)[甲乙丙丁戊己庚辛壬癸一二三四五六七八九十\d]*",
                re.sub(r"\s+", "", name),
            )
        )

    @staticmethod
    def _location_key(name: str) -> str:
        value = re.sub(r"\s+", "", name)
        value = re.sub(r"[江河溪湖海]", "水", value)
        return re.sub(r"[的之]", "", value)

    @classmethod
    def _canonical_location_name(cls, name: str, canonical_names: list[str]) -> str:
        if not name or name in canonical_names:
            return name
        key = cls._location_key(name)
        matches = [
            canonical
            for canonical in canonical_names
            if key in cls._location_key(canonical)
            or cls._location_key(canonical) in key
        ]
        return matches[0] if len(matches) == 1 else name

    @staticmethod
    def _dialogue_source_span(text: str, source: str) -> str | None:
        """Find one contiguous source span containing ordered quoted speech.

        Novel dialogue is often interrupted by tags such as “他说道”.  A TTS
        turn may join those adjacent quotes while its evidence span keeps the
        intervening prose, preserving an exact source trace.
        """

        target = re.sub(r"\s+", "", text)
        quoted = list(re.finditer(r"[“\"]([^”\"]+)[”\"]", source))
        for start in range(len(quoted)):
            joined = ""
            for end in range(start, min(start + 3, len(quoted))):
                joined += re.sub(r"\s+", "", quoted[end].group(1))
                if joined == target:
                    span = source[quoted[start].start() : quoted[end].end()]
                    return span if len(span) <= 500 else None
                if len(joined) > len(target):
                    break
        return None

    def _canonicalize_characters(self, plan: EpisodePlan, bible: StoryBible) -> EpisodePlan:
        canonical_names = [character.name for character in bible.characters]
        shots = []
        for shot in plan.shots:
            turns = []
            for turn in shot.turns:
                if turn.speaking:
                    speaker = self._canonical_character_name(turn.speaker_name, canonical_names)
                    if speaker not in canonical_names and self._is_anonymous_crowd(speaker):
                        turns.append(
                            turn.model_copy(
                                update={
                                    "speaker_name": "旁白",
                                    "role": "narrator",
                                    "speaking": False,
                                    "emotion": f"画外群声·{turn.emotion}",
                                }
                            )
                        )
                    else:
                        turns.append(
                            turn.model_copy(update={"speaker_name": speaker, "role": speaker})
                        )
                else:
                    turns.append(turn)
            characters = []
            for character in shot.characters:
                canonical = self._canonical_character_name(character, canonical_names)
                if canonical not in canonical_names and self._is_anonymous_crowd(canonical):
                    continue
                if canonical not in characters:
                    characters.append(canonical)
            location = self._canonical_location_name(shot.location, bible.locations)
            shots.append(
                shot.model_copy(
                    update={"characters": characters, "turns": turns, "location": location}
                )
            )
        showrunner = plan.showrunner_plan
        if showrunner is not None:
            information_states = []
            for fact in showrunner.information_states:
                awareness = [
                    item.model_copy(
                        update={
                            "character_name": self._canonical_character_name(
                                item.character_name, canonical_names
                            )
                        }
                    )
                    for item in fact.character_awareness
                ]
                information_states.append(
                    fact.model_copy(update={"character_awareness": awareness})
                )
            deltas = [
                delta.model_copy(
                    update={
                        "character_name": self._canonical_character_name(
                            delta.character_name, canonical_names
                        )
                    }
                )
                for delta in showrunner.character_state_deltas
            ]
            showrunner = showrunner.model_copy(
                update={
                    "information_states": information_states,
                    "character_state_deltas": deltas,
                }
            )
        return plan.model_copy(update={"shots": shots, "showrunner_plan": showrunner})

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
                turn_source_quote = (
                    turn.source_quote
                    if turn_quote and turn_quote in normalized_source
                    else quote
                )
                if turn.speaking and re.sub(r"\s+", "", turn.text) not in re.sub(
                    r"\s+", "", turn_source_quote
                ):
                    dialogue_span = self._dialogue_source_span(turn.text, source)
                    if dialogue_span is not None:
                        turn_source_quote = dialogue_span
                    elif turn.derivation != TurnDerivation.DERIVED:
                        # A verbatim claim that cannot be traced to quoted
                        # chapter speech must not pass as character dialogue.
                        # This demotion used to hit every staged line as well
                        # and silently rewrote it into a narrator turn — which
                        # is exactly how quoted lines, first-person text and an
                        # invented farewell ended up voiced by 旁白 in earlier
                        # plans.  Derived turns declare the staging openly and
                        # are policed by the script gates instead, so they keep
                        # their character voice.
                        turn = turn.model_copy(
                            update={
                                "role": "narrator",
                                "speaker_name": "旁白",
                                "speaking": False,
                            }
                        )
                grounded_turns.append(
                    turn.model_copy(update={"source_quote": turn_source_quote})
                )
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
        plan = self._ground_quotes(
            self._canonicalize_characters(EpisodePlan.model_validate(data), bible),
            episode.source_text,
            bible,
        )
        # Correct the decidable mislabels before judging the draft, so revision
        # rounds are spent on writing rather than on bookkeeping the controller
        # can do itself.
        plan = repair_machine_draft(plan, episode)
        normalized_source = re.sub(r"\s+", "", episode.source_text)
        canonical_names = {character.name for character in bible.characters}
        canonical_locations = set(bible.locations)
        issues: list[dict[str, object]] = []
        if plan.showrunner_plan is not None:
            for fact_index, fact in enumerate(plan.showrunner_plan.information_states):
                unknown_awareness = sorted(
                    {
                        item.character_name
                        for item in fact.character_awareness
                        if item.character_name not in canonical_names
                    }
                )
                if unknown_awareness:
                    issues.append({
                        "field": (
                            f"showrunner_plan.information_states.{fact_index}."
                            "character_awareness"
                        ),
                        "message": "must use StoryBible character names",
                        "unknown": unknown_awareness,
                    })
            for delta_index, delta in enumerate(
                plan.showrunner_plan.character_state_deltas
            ):
                if delta.character_name not in canonical_names:
                    issues.append({
                        "field": (
                            f"showrunner_plan.character_state_deltas.{delta_index}."
                            "character_name"
                        ),
                        "message": "must use a StoryBible character name",
                        "unknown": delta.character_name,
                    })
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
            if shot.performance_plan is None:
                issues.append({
                    "field": f"shots.{shot.index}.performance_plan",
                    "message": "is required and must describe causal action beats",
                })
            if shot.camera_plan is None:
                issues.append({
                    "field": f"shots.{shot.index}.camera_plan",
                    "message": (
                        "is required and must choose locked, motivated_subtle, or "
                        "motivated_emphasis with a narrative motivation and stable action axis"
                    ),
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
                if (
                    turn.speaking
                    and turn.derivation != TurnDerivation.DERIVED
                    and re.sub(r"\s+", "", turn.text) not in turn_quote
                    and self._dialogue_source_span(turn.text, turn.source_quote) is None
                ):
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.text",
                        "message": "visible dialogue must occur verbatim inside "
                        "source_quote; if this line stages narration as speech, "
                        "declare derivation=derived and cite that narration",
                    })
        if issues:
            raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
        return plan

    def _validate_showrunner_data(
        self,
        data: dict,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
    ) -> ShowrunnerPlan:
        showrunner = ShowrunnerPlan.model_validate(data)
        canonical_names = [character.name for character in bible.characters]
        information_states = []
        for fact in showrunner.information_states:
            awareness = [
                item.model_copy(
                    update={
                        "character_name": self._canonical_character_name(
                            item.character_name, canonical_names
                        )
                    }
                )
                for item in fact.character_awareness
            ]
            information_states.append(
                fact.model_copy(update={"character_awareness": awareness})
            )
        deltas = [
            delta.model_copy(
                update={
                    "character_name": self._canonical_character_name(
                        delta.character_name, canonical_names
                    )
                }
            )
            for delta in showrunner.character_state_deltas
        ]
        showrunner = showrunner.model_copy(
            update={
                "planning_mode": "planner",
                "information_states": information_states,
                "character_state_deltas": deltas,
            }
        )
        source = re.sub(r"\s+", "", episode.source_text)
        event_ids = {event.event_id for event in diagnosis.events}
        issues: list[dict[str, object]] = []

        def grounded(value: str) -> bool:
            return bool(value and re.sub(r"\s+", "", value) in source)

        # Character names already get controller-side canonicalisation above;
        # quotes relied on the model copying chapter text to the character.
        # A 27B planner reliably paraphrases one beat per attempt and the
        # repair loop never converges.  The semantic binding is event_ids —
        # validated on their own below — so when a quote is not verbatim but
        # the entry points at diagnosed events, substitute the event's already
        # validated source_quote instead of failing the whole stage.
        event_quotes = {event.event_id: event.source_quote for event in diagnosis.events}

        def anchored_quote(quote: str, ids: list[str]) -> str:
            if grounded(quote):
                return quote
            for event_id in ids:
                candidate = event_quotes.get(event_id, "")
                if grounded(candidate):
                    return candidate
            return quote

        showrunner = showrunner.model_copy(
            update={
                "retention": showrunner.retention.model_copy(
                    update={
                        "beats": [
                            beat.model_copy(
                                update={
                                    "source_quote": anchored_quote(
                                        beat.source_quote, beat.event_ids
                                    )
                                }
                            )
                            for beat in showrunner.retention.beats
                        ]
                    }
                ),
                "information_states": [
                    fact.model_copy(
                        update={
                            "source_quote": anchored_quote(
                                fact.source_quote, fact.source_event_ids
                            )
                        }
                    )
                    for fact in showrunner.information_states
                ],
                "character_state_deltas": [
                    delta.model_copy(
                        update={
                            "source_quote": anchored_quote(
                                delta.source_quote, delta.event_ids
                            )
                        }
                    )
                    for delta in showrunner.character_state_deltas
                ],
            }
        )
        fact_ids = {fact.fact_id for fact in showrunner.information_states}
        beat_ids = {beat.beat_id for beat in showrunner.retention.beats}

        starts = [beat.target_start_ratio for beat in showrunner.retention.beats]
        points = [0.0, *starts, 1.0]
        max_gap = max(
            (right - left for left, right in zip(points, points[1:])),
            default=1.0,
        )
        if max_gap > showrunner.retention.max_attention_gap_ratio + 0.01:
            issues.append({
                "field": "retention.beats",
                "message": "retention beats leave a middle attention gap above budget",
                "max_gap_ratio": round(max_gap, 6),
            })
        functions = {beat.function for beat in showrunner.retention.beats}
        if not {"hook", "question", "cliffhanger"} <= functions or not functions & {
            "payoff",
            "reversal",
        }:
            issues.append({
                "field": "retention.beats",
                "message": "must include hook, question, payoff or reversal, and cliffhanger",
            })
        if not any(
            beat.function == "hook" and beat.target_start_ratio <= 0.05
            for beat in showrunner.retention.beats
        ):
            issues.append({"field": "retention.beats", "message": "hook must start in first 5%"})
        if not any(
            beat.function == "cliffhanger" and beat.target_start_ratio >= 0.8
            for beat in showrunner.retention.beats
        ):
            issues.append({
                "field": "retention.beats",
                "message": "cliffhanger must start in final 20%",
            })
        for index, beat in enumerate(showrunner.retention.beats):
            if beat.shot_indexes:
                issues.append({
                    "field": f"retention.beats.{index}.shot_indexes",
                    "message": "must stay empty until screenplay shots exist",
                })
            if not grounded(beat.source_quote):
                issues.append({
                    "field": f"retention.beats.{index}.source_quote",
                    "message": "must be an exact current-chapter excerpt",
                })
            if not beat.event_ids or not set(beat.event_ids) <= event_ids:
                issues.append({
                    "field": f"retention.beats.{index}.event_ids",
                    "message": "must reference diagnosed current-chapter events",
                })
            if set(beat.new_information_fact_ids) - fact_ids:
                issues.append({
                    "field": f"retention.beats.{index}.new_information_fact_ids",
                    "message": "references unknown information fact ids",
                })
        if not showrunner.information_states:
            issues.append({
                "field": "information_states",
                "message": "at least one explicit viewer/character information state is required",
            })
        for index, fact in enumerate(showrunner.information_states):
            # One bundled message per condition group starved the repair loop
            # of signal: the model could not tell which requirement it had
            # missed and repeated the same mistake every revision.
            fact_problems: list[str] = []
            if not grounded(fact.source_quote):
                fact_problems.append(
                    "source_quote is not a verbatim current-chapter excerpt; "
                    "copy the matching diagnosis event's source_quote exactly, "
                    "including punctuation"
                )
            if not set(fact.source_event_ids) <= event_ids:
                fact_problems.append(
                    "source_event_ids reference unknown events: "
                    + ",".join(sorted(set(fact.source_event_ids) - event_ids))
                )
            if fact_problems:
                issues.append({
                    "field": f"information_states.{index}",
                    "message": "; ".join(fact_problems),
                })
            if fact.reveal_beat_id and fact.reveal_beat_id not in beat_ids:
                issues.append({
                    "field": f"information_states.{index}.reveal_beat_id",
                    "message": "references an unknown retention beat",
                })
            unknown = sorted(
                {
                    item.character_name
                    for item in fact.character_awareness
                    if item.character_name not in canonical_names
                }
            )
            if unknown:
                issues.append({
                    "field": f"information_states.{index}.character_awareness",
                    "message": "must use StoryBible character names",
                    "unknown": unknown,
                })
        for index, delta in enumerate(showrunner.character_state_deltas):
            delta_problems: list[str] = []
            if delta.character_name not in canonical_names:
                delta_problems.append(
                    f"character_name '{delta.character_name}' is not a StoryBible "
                    "character; use one of: " + "、".join(sorted(canonical_names))
                )
            if not grounded(delta.source_quote):
                delta_problems.append(
                    "source_quote is not a verbatim current-chapter excerpt; "
                    "copy the matching diagnosis event's source_quote exactly, "
                    "including punctuation"
                )
            if not set(delta.event_ids) <= event_ids:
                delta_problems.append(
                    "event_ids reference unknown events: "
                    + ",".join(sorted(set(delta.event_ids) - event_ids))
                )
            if delta.before == delta.after:
                delta_problems.append("before and after are identical; drop the delta if nothing changed")
            if delta_problems:
                issues.append({
                    "field": f"character_state_deltas.{index}",
                    "message": "; ".join(delta_problems),
                })
        expected_delta_events = {
            event.event_id
            for event in diagnosis.events
            if event.state_change and event.characters
        }
        covered_delta_events = {
            event_id
            for delta in showrunner.character_state_deltas
            for event_id in delta.event_ids
        }
        if expected_delta_events - covered_delta_events:
            issues.append({
                "field": "character_state_deltas",
                "message": "must cover diagnosed character state changes",
                "missing_event_ids": sorted(expected_delta_events - covered_delta_events),
            })
        if issues:
            raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
        return showrunner

    def _plan_showrunner(
        self,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
        previous_state: SeriesState | None,
    ) -> ShowrunnerPlan:
        schema = ShowrunnerPlan.model_json_schema()
        system = (
            "你是商业竖屏短剧的Showrunner，只做分镜之前的观众、信息和人物状态决策，不写具体镜头提示词。"
            "只使用当前章与上一集已确认状态，不得引用后文。planning_mode=planner。"
            "retention使用4-8个0-1相对时间节点，包含前5%的hook、question、至少一次payoff或reversal、"
            "后20%的cliffhanger。节点的target_start_ratio必须大致均匀铺满整条0到1："
            "把0、各节点起始位置、1排成一列，任意相邻两个数之差都不得超过"
            "max_attention_gap_ratio（默认0.25）——注意0到第一个节点、"
            "最后一个节点到1这两段也要算。例如6个节点可取0.0、0.18、0.36、0.54、0.7、0.85；"
            "不要把节点挤在开头和结尾而让中段留出大空窗。此阶段还没有镜头编号，"
            "shot_indexes必须留空；每个节点只绑定event_ids、逐字source_quote、观众问题、承诺、新信息和情绪变化。"
            "information_states逐条记录事实真假、观众认知、各角色认知或误解、信息差用途和揭示节点。"
            "character_state_deltas只记录当前章确实改变的社会地位、关系、力量、情绪、信心或服装；"
            "before与after不得相同，永久脸型发型不属于剧情状态。所有事实必须有当前章逐字证据。"
            "所有source_quote一律从章节诊断中对应event的source_quote整段逐字复制（包含全部标点和引号），"
            "不要自己重新摘抄、缩写或改写原文——校验按逐字匹配执行，改一个标点都会失败。"
            "character_name和character_awareness必须逐字使用故事圣经characters里的角色名，不得用简称或别名。"
            "以下字段只能从给定取值里选，不得自造，也不要翻译成中文："
            "retention.beats.function取hook、question、pressure、escalation、payoff、reversal、cliffhanger之一；"
            "information_states.truth_status取confirmed、potential、misread之一；"
            "information_states.viewer_awareness和character_awareness.awareness取"
            "knows、suspects、misled、unaware之一；"
            "information_states.dramatic_use取viewer_leads（观众先知道）、character_leads（角色先知道）、"
            "simultaneous_reveal（同步揭示）、misunderstanding（误会）、withheld（暂时隐瞒）之一。"
            "严格输出JSON。"
        )
        user = (
            f"当前集：{episode.index} {episode.source_title}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"故事圣经：{bible.model_dump_json()}\n当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "plan_showrunner",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=SHOWRUNNER_TOKEN_BUDGET
            ),
            lambda data: self._validate_showrunner_data(
                data, episode, diagnosis, bible
            ),
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        schema = EpisodePlan.model_json_schema()
        source_chars = len(re.sub(r"\s+", "", episode.source_text))
        # State the floor the gate actually enforces, not just a target range:
        # drafts kept landing at half the lower bound because a range reads as
        # advisory.
        floor = script_policy(
            source_chars, "balanced", self.settings.creative_profile
        )
        if source_chars <= 1200:
            size_guidance = "全片旁白与对白合计450-750个汉字、8-14个镜头"
        elif source_chars <= 3000:
            size_guidance = "全片旁白与对白合计700-1100个汉字、16-24个镜头"
        else:
            size_guidance = "全片旁白与对白合计900-1400个汉字、24-36个镜头"
        size_guidance += (
            f"。硬性下限：turns文本合计不得少于{floor.min_script_chars}个汉字、"
            f"不得少于{floor.min_turns}个turn、不得少于{floor.min_shots}个镜头，"
            "低于任何一项都会被直接打回；写够是第一优先，宁可多写一两个反应镜头"
        )
        system = (
            "你是小说改编漫剧编剧。忠于原文人物关系、关键事件、顺序、因果和结局；不得新增核心情节。"
            f"不使用静态片头，0-3秒直接进入当前章有原文依据的冲突或悬念。原文有效字数约{source_chars}，{size_guidance}；"
            "不得为凑镜头或字数重复情节、虚构事件或拆碎同一句话。"
            "每个镜头设置 turns：旁白 role=narrator、speaking=false；人物对白 role 和 speaker_name 均使用角色原名、"
            "speaking=true、delivery_mode=visible_dialogue；内心声或画外对白使用角色原名、"
            "speaking=false，并分别设置delivery_mode=inner_voice或offscreen_dialogue。"
            "原文带引号的台词一律归给具体角色，不得标成旁白，并设置derivation=verbatim逐字引用；"
            "承载因果、来历或转折的叙述段落设置derivation=derived，改写成角色真会说出口的话或可拍摄的反应，"
            "并在source_quote里引用它依据的那段叙述。"
            "严禁把原文带引号的台词概括成一句旁白（例如把五句嘲讽压成“众人纷纷嘲讽他”）；"
            "这些台词必须由具体角色逐句说出。"
            "示范：原文叙述“三年之前，这名声望达到巅峰的天才少年，却是突兀地接受到了有生以来最残酷的打击，"
            "不仅辛辛苦苦修炼十数载方才凝聚的气旋，一夜之间，化为乌有”——"
            "错误做法是写成一句旁白“三年前他的气旋一夜消失”；"
            "正确做法是拆成在场两个角色的一问一答，每个turn都是角色对白、derivation=derived、"
            "source_quote引用这段叙述：甲“他以前，真有传的那么厉害？”／"
            "乙“十一岁凝聚气旋，家族百年之内最年轻。”／甲“那后来呢？”／"
            "乙“三年前，一夜之间，全没了。”／甲“为什么？”／乙“没人知道。”。"
            "悬念要让角色问出来，不能由旁白直接告诉观众。"
            "全片至少三分之一的台词字数应来自derived角色对白；只把derived用在旁白上等于没有改编。"
            "一个turn是一口气可自然说完的完整语义句，通常12-36字，硬上限60字；"
            "不得为了字幕长度拆碎句子。字幕在音频对齐后独立分页，每页最多两行且仍逐字来自turn.text。"
            "一个连续镜头通常承载1-3个语义turn，对话场景尤其要写成有来有回的多轮：""甲问一句、乙答一句、甲再追问，这些都放在同一个镜头的turns数组里，""而不是一镜只放一句然后靠加镜头凑数。""短剧节奏来自信息、动作和情绪变化，不来自机械断句或增加切镜；"
            "每个镜头必须填写 performance_plan：动作起点、1-4个有触发和反应的 motion_beats、动作终点；"
            "必须填写camera_plan.mode、motivation、action_axis和screen_direction。默认mode=locked，"
            "由人物表演承担动态；只有人物明确位移、信息揭示或情绪/权力转折才使用motivated_subtle，"
            "章节高潮或关键反转才少量使用motivated_emphasis。每镜最多一条短轨迹，完成后停住；"
            "同场对话始终在行动轴同侧，人物左右和视线方向不得无故交换。"
            "参考图只锁人物身份、服装、环境和画风，不能锁静态姿势、构图或机位。"
            "覆盖本章开端、发展、高潮和结尾。source_quote 必须逐字摘自本章。画面健康克制，无色情、政治和血腥。"
            "严格输出 JSON。"
        )
        user = (
            f"小说：{novel.title}\n本集：{episode.source_title}\n故事圣经：{bible.model_dump_json()}\n"
            f"原文：{episode.source_text}\nJSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        plan = _bounded_validate(
            "plan_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(system, user, repair),
            lambda data: self._validate_episode_data(data, episode, bible),
        )
        return apply_creative_direction(
            plan,
            deterministic_chapter_diagnosis(episode),
            bible,
            profile=self.settings.creative_profile,
        )

    def _diagnose_episode(
        self,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None,
    ) -> ChapterDiagnosis:
        schema = ChapterDiagnosis.model_json_schema()
        evidence_bank = "\n".join(
            f"E{index:03d}\t{row}"
            for index, row in enumerate(source_evidence_units(episode.source_text), 1)
        )
        system = (
            "你是逐章漫剧改编的事实编辑。只分析当前章节，不得推测或使用后续章节。"
            "把章节提炼为按原文顺序排列的关键事件表，关键事件必须覆盖开端、人物建立、"
            "冲突发展、因果转折、高潮和章末结果。每个事件引用当前章的精确原文，"
            "hook_source_quote和每个event.source_quote都必须从SOURCE_EVIDENCE中选择一整行逐字复制，"
            "不得概括、缩写、拼接或修改标点；description才用于概括事件。"
            "每条source_quote硬上限500字符，而SOURCE_EVIDENCE中每一行都远短于此，"
            "所以一旦超长就说明你拼接了多行——只复制其中一行。"
            "causes只能引用更早的事件。未知用途细节标为potential_foreshadowing，不得擅自删除。"
            "event.characters只能填写故事圣经characters里的具名角色原名；"
            "人群、众人、族人这类无名群体不是具名角色，不要写进characters，"
            "他们的言行写进description即可。"
            "previous_state只用于连续性，不得当作本集新增剧情。严格输出JSON。"
        )
        user = (
            f"当前章节：{episode.source_title}\n当前章原文：{episode.source_text}\n"
            f"SOURCE_EVIDENCE（source_quote只能逐字复制其中一整行）：\n{evidence_bank}\n"
            f"系列设定：{bible.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            "事件数量按语义决定，通常12-30个；不要把每一句描写都机械列成事件。"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "diagnose_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=DIAGNOSIS_TOKEN_BUDGET
            ),
            lambda data: validate_chapter_diagnosis(
                ChapterDiagnosis.model_validate(data), episode, bible
            ),
        )

    def _review_episode(
        self,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> ScriptQualityReport:
        schema = ScriptQualityReport.model_json_schema()
        system = (
            "你是独立的漫剧剧本审稿人，不负责美化分镜。检查剧本是否忠于当前章、"
            "是否先铺垫再兑现、主要人物是否在承担冲突前完成身份和立场建立、"
            "结尾是否停在当前章边界，以及是否使用后文剧情。"
            "short-drama-adaptive-v1允许0-3秒展示当前章内的结果、公开受压或关系异常作为冷开场，"
            "但cold_open_source_quote必须来自当前章，前两镜必须实际呈现，随后必须补足原因并在正常因果位置再次兑现；"
            "不得借用后文章节的答案。"
            "请站在从未读过原著的观众角度，确认能回答：主要人物是谁、人物关系是什么、"
            "发生了什么、为什么发生、造成什么后果、人物为何这样反应。"
            "检查旁白是否超过dramaturgy.narration_budget_ratio；能用动作、对白、反应、道具或环境结果表达的信息，"
            "不得继续写成解释性旁白。"
            "检查showrunner_plan：留存节点从hook、question、escalation到payoff/reversal和cliffhanger连续分布，"
            "相邻节点不得留下超过retention.max_attention_gap_ratio的中段空窗；每个information_state必须回答"
            "观众知道什么、角色知道或误解什么，并有当前章逐字证据；character_state_delta只记录当前章真正改变的"
            "社会地位、关系、力量、情绪、信心或服装状态，不得把永久长相当作剧情状态。"
            "检查每镜shot_intent是否绑定留存节点、观众焦点和信息事实；audio_beats必须由台词、动作、揭示或反应触发，"
            "不得机械地每隔几秒堆冲击音。"
            "每个turn应是一口气可自然说完、只承载一个核心事实、动作或反应的完整语义句；通常12-36字，"
            "超过60字才因TTS与镜头时长风险判为blocking。不得为了两行字幕把一句话切碎。"
            "长镜头可以连续承载多个语义turn，不能因拆台词而要求增加切镜。"
            "只要存在缺失关键因果、突兀结论、人物动机不明、提前剧透或未来剧情，"
            "passed必须为false并给blocking issue。"
            "计数值可按输入填写，程序会重新计算。严格输出JSON。"
        )
        user = (
            f"当前章原文：{episode.source_text}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"待审剧本：{plan.model_dump_json()}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        qualitative = _bounded_validate(
            "review_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=REVIEW_TOKEN_BUDGET
            ),
            ScriptQualityReport.model_validate,
        )
        return evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            qualitative=qualitative,
            previous_state=previous_state,
        )

    def _expand_script_turns(
        self,
        episode: Episode,
        bible: StoryBible,
        plan: EpisodePlan,
        required_chars: int,
    ) -> EpisodePlan:
        schema = ScriptExpansion.model_json_schema()
        current_chars = sum(
            len(re.sub(r"\s+", "", turn.text))
            for shot in plan.shots
            for turn in shot.turns
        )
        compact_shots = [
            {
                "shot_index": shot.index,
                "event_ids": shot.event_ids,
                "characters": shot.characters,
                "source_quote": shot.source_quote,
                "turns": [turn.model_dump(mode="json") for turn in shot.turns],
            }
            for shot in plan.shots
        ]
        system = (
            "你是漫剧台词编辑。只补写现有镜头的turns，不得修改镜头顺序、事件、人物关系或结局。"
            "原文带引号的台词归具体角色并设置derivation=verbatim逐字引用，不得标成旁白；"
            "叙述段落设置derivation=derived，改写成角色对白或可拍摄的反应，并引用所依据的叙述句，"
            "不得引入原文没有的事实。"
            "优先补原文已有的短对白、内心声或必要因果，不得用摘要旁白填充字数；"
            "能由visual_prompt和performance_plan表演的信息不要重复朗读。"
            "每个turn只讲一个核心事实、动作或反应，同时必须保持自然完整的语义与呼吸，通常12-36字，硬上限60字。"
            "不得为字幕分页切碎完整句；字幕由音频对齐层另行分页。"
            "同一shot通常用1-3个语义turn承载一个连续表演beat，必要时可更多，但不要增加shot或切镜。"
            "每个shot总turns.text按实际动作时长决定，不为字数填充。只返回需要替换的shot_index和完整turns数组。"
            "严格输出JSON。"
        )
        user = (
            f"当前有效字数：{current_chars}；最低目标：{required_chars}；建议目标："
            f"{required_chars + 100}。\n当前章原文：{episode.source_text}\n"
            f"角色标准名：{json.dumps([item.name for item in bible.characters], ensure_ascii=False)}\n"
            f"待补写镜头：{json.dumps(compact_shots, ensure_ascii=False)}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        expansion = _bounded_validate(
            "expand_script_turns",
            1,
            lambda repair: self._json(
                system,
                user,
                repair,
                token_budget=SCRIPT_EXPANSION_TOKEN_BUDGET,
            ),
            ScriptExpansion.model_validate,
        )
        patches = {patch.shot_index: patch.turns for patch in expansion.shots}
        unknown = sorted(set(patches) - {shot.index for shot in plan.shots})
        if unknown:
            raise ValueError(f"script expansion uses unknown shot indexes: {unknown}")
        expanded = plan.model_copy(
            update={
                "shots": [
                    shot.model_copy(update={"turns": patches.get(shot.index, shot.turns)})
                    for shot in plan.shots
                ]
            }
        )
        return self._validate_episode_data(expanded.model_dump(mode="json"), episode, bible)

    def _update_series_state(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> SeriesState:
        schema = SeriesState.model_json_schema()
        system = (
            "你是连续剧状态管理员。根据当前章已经发生的事实更新完整series_state快照。"
            "新事实必须附当前章精确原文和当前集编号；历史事实必须原样继承上一状态，"
            "不得把推测写成confirmed，不得写入后文秘密。服装、位置、伤势、知识、关系、"
            "社会地位、力量、信心、道具和未解悬念只在当前章有依据时改变。"
            "将showrunner_plan.character_state_deltas提交到对应characters状态，将当前集information_states"
            "转为带source_episode证据的跨集information_states；同一事实后续只更新知情者或认知状态，不重复造事实。"
            "严格输出JSON。"
        )
        user = (
            f"当前集编号：{episode.index}\n当前章节：{episode.source_title}\n"
            f"当前章原文：{episode.source_text}\n系列设定：{bible.model_dump_json()}\n"
            f"上一状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n已审核剧本：{plan.model_dump_json()}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "update_series_state",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=SERIES_STATE_TOKEN_BUDGET
            ),
            lambda data: validate_series_state(
                SeriesState.model_validate(data), episode, previous_state
            ),
        )

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        diagnosis = self._diagnose_episode(episode, bible, previous_state)
        draft_root = (
            self.settings.output_root.resolve()
            / novel.novel_id
            / "script_drafts"
            / f"episode_{episode.index:03d}"
        )
        atomic_write_json(
            draft_root / "chapter_diagnosis.json",
            diagnosis.model_dump(mode="json"),
        )
        showrunner = (
            self._plan_showrunner(episode, diagnosis, bible, previous_state)
            if self.settings.creative_profile == SHORT_DRAMA_PROFILE
            else None
        )
        if showrunner is not None:
            atomic_write_json(
                draft_root / "showrunner_plan.json",
                showrunner.model_dump(mode="json"),
            )
        schema = EpisodePlan.model_json_schema()
        source_chars = len(re.sub(r"\s+", "", episode.source_text))
        policy = script_policy(
            source_chars,
            diagnosis.density,
            self.settings.creative_profile,
        )
        size_guidance = (
            f"至少{policy.min_script_chars}个有效发声字、至少{policy.min_turns}个turn、"
            f"至少{policy.min_shots}个shot；只在有新信息、动作或反应时增加"
        )
        direction_brief = creative_prompt_brief(bible.genre)
        system = (
            "你是连续竖屏短剧的逐章编剧和改编导演，不是有声书摘要员。当前章完整对应当前一集，不得拆集、"
            "合并下一章或借用后文事件；保留原文事实、人物关系、关键因果和章末边界，但可重排当前章信息。"
            f"creative_profile必须填写{self.settings.creative_profile}。{direction_brief}"
            "先填写dramaturgy，只选择一个dramatic_question和3-5个冲突节点；"
            "cold_open必须在0-3秒呈现当前章内最易读的受压结果、关系异常、关键道具或行动后果，"
            "cold_open_source_quote必须逐字取自当前章的某一整行，且这一行要短——""它必须能完整出现在前两镜的source_quote里（每镜source_quote上限120字），""所以不要选很长的段落做冷开场证据；前两镜必须实际呈现它。""如果预览后段事件，随后回到原因，"
            "并在正常因果位置再次完整兑现。不得提前给出后文章节答案。"
            "Showrunner已经独立完成留存、信息差和人物状态决策；必须按输入中的showrunner_plan安排事件和镜头，"
            "不得在剧本阶段重写其事实、时间节点或人物状态。showrunner_plan字段可原样复制，程序会依据event_ids"
            "确定性绑定最终shot_indexes。不得为了填留存节点虚构刺激。"
            f"有效剧本目标为{size_guidance}。每个shot必须填写event_ids，"
            "每个章节事件必须写入adaptation_ledger，critical事件不得removed。"
            "承载因果、动机、来历或转折的叙述事件用externalized，把叙述改写成可见对白、反应或道具结果；"
            "supporting事件可compressed或merged，texture事件可removed；不要为覆盖原文把所有句子都发声。"
            "小说通常只有两成文字带引号，其余是叙述。把叙述一律压成旁白会让因果消失，"
            "所以每个turn必须声明derivation："
            "derivation=verbatim时turn.text必须逐字出现在source_quote的引号内容里；"
            "derivation=derived时source_quote必须是含叙述的原文，你据此把叙述外化为该场景中"
            "某个角色真的会说出口的话、或一次可拍摄的反应，不得引入原文没有的事实、人物或事件。"
            "不得把原文引号内的台词改写成近似句：台词要么逐字引用，要么由叙述外化。"
            "带引号的原文台词必须归给具体角色，绝不能标成旁白。"
            "旁白只保留无法表演的时间、空间、必要规则和内心转折；能用动作、人物对白、反应、道具结果"
            "表达的信息必须改写成derived对白或反应，而不是压成旁白。"
            "旁白字数占比不得超过dramaturgy.narration_budget_ratio。"
            "使用口语化短剧节拍：每个turn只交付一个核心事实、动作或反应，但必须是一口气自然说完的完整语义句，"
            "通常12-36字，硬上限60字；字幕在音频对齐后独立切页，严禁为字幕长度把一句话拆碎；"
            "需要讲因果时按触发→事实→后果→人物反应排列，不得只写模糊情绪。"
            "长段来历、回忆或规则说明不要交给单个角色一口气独白，"
            "拆成有听者的一问一答，让悬念由角色问出来。"
            "每个turn只允许一个声音角色；可见对白设置visible_dialogue；"
            "原文中的内心声和画外对白可用角色音色，但speaking必须为false并设置inner_voice或offscreen_dialogue。"
            "每镜performance_plan按触发→察觉→一个主要动作→对方反应→收束组织；"
            "不要为了防静态而让人物每1-2秒机械地转头、摆手或改变重心。"
            "camera_plan默认locked并用1-2个短camera_beats，只有明确叙事动机才选择移动模式；"
            "移动镜头不超过约三分之一、强调运镜不超过约十分之一，不得连续两镜都明显运镜；"
            "运镜动机只允许空间揭示、明确位移、视点变化或权力/情绪转折；普通对白和反应保持固定机位。"
            "同场景锁定行动轴、人物左右和视线方向。"
            "每镜shot_intent必须解释dramatic_function、power_relation、emotion_target、viewer_focus，"
            "dramatic_function只能从establish/advance/pressure/withhold/reveal/payoff/reaction/transition/cliffhanger中选，"
            "不得使用reversal/climax/escalation/hook/question等留存节点名称；"
            "并绑定retention_beat_id；承担信息揭示的镜头引用information_fact_ids。先有语义意图，再选择景别和机位。"
            "visual_strategy必须明确：单人普通对白/反应用direct-assets，无人物空镜用scene-only；"
            "多人精确站位、人物道具交互、结果揭示、高潮反转和封面级构图用story-keyframe，"
            "并在keyframe_reasons记录原因。audio_plan中speech_strategy当前统一locked以保证原文台词准确；"
            "TTS只负责真正发声的turn，ambience、music_cue和sfx_events写场景声音意图，不得把音效写成旁白。"
            "audio_beats使用0-1相对位置，cue_type只能取silence、ambience、impact、music_rise、music_cut、"
            "bass_drop、heartbeat、sfx、duck、release之一，不得自造也不要用中文；"
            "performance_plan.motion_beats.phase和camera_plan.camera_beats.phase只能取"
            "opening、development、resolution之一；"
            "每个声音变化必须写明台词、动作、揭示或反应触发，并绑定本镜留存节点，不得机械铺满整镜。"
            "不要重复Schema说明，不要在字段中写长篇方法论。"
            "对于长章节，每个连续shot通常承载1-3个语义turn，只表达一个明确视觉或情绪beat；"
            "优先讲清关键因果，不用文学性外貌铺陈或摘要旁白凑字数；"
            "字数只统计turns.text，narration、subtitle、visual_prompt不计入有效剧本字数。"
            "前10秒必须建立人物、异常和即时问题，章末反转必须先铺垫后兑现。严格输出JSON。"
        )
        base_user = (
            f"小说：{novel.title}\n当前章节：{episode.source_title}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"独立Showrunner计划：{showrunner.model_dump_json() if showrunner else '{}'}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"故事圣经：{bible.model_dump_json()}\n当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        repair: dict | None = None
        last_report: ScriptQualityReport | None = None
        for revision in range(self.settings.planner_max_revisions + 1):
            draft_number = revision + 1
            data = self._json(
                system,
                base_user,
                repair,
                token_budget=SCRIPT_TOKEN_BUDGET,
            )
            atomic_write_json(draft_root / f"draft_{draft_number:02d}.json", data)
            try:
                plan = self._validate_episode_data(data, episode, bible)
                if showrunner is not None:
                    plan = plan.model_copy(update={"showrunner_plan": showrunner})
                plan = apply_creative_direction(
                    plan,
                    diagnosis,
                    bible,
                    profile=self.settings.creative_profile,
                )
                plan = normalize_chronological_plan(plan, diagnosis, episode)
                atomic_write_json(
                    draft_root / f"draft_{draft_number:02d}_normalized.json",
                    plan.model_dump(mode="json"),
                )
                deterministic = evaluate_script_quality(
                    plan, diagnosis, episode, previous_state=previous_state
                )
                issue_codes = {issue.code for issue in deterministic.issues}
                if not deterministic.passed and issue_codes <= {
                    "script_too_short",
                    "too_few_turns",
                }:
                    required_chars = script_policy(
                        len(re.sub(r"\s+", "", episode.source_text)),
                        diagnosis.density,
                        self.settings.creative_profile,
                    ).min_script_chars
                    plan = self._expand_script_turns(
                        episode, bible, plan, required_chars
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_expanded.json",
                        plan.model_dump(mode="json"),
                    )
                    deterministic = evaluate_script_quality(
                        plan, diagnosis, episode, previous_state=previous_state
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_expanded_validation.json",
                        deterministic.model_dump(mode="json"),
                    )
                if not deterministic.passed:
                    raise ValueError(deterministic.model_dump_json())
                report = self._review_episode(episode, diagnosis, plan, previous_state)
                last_report = report
                atomic_write_json(
                    draft_root / f"draft_{draft_number:02d}_validation.json",
                    report.model_dump(mode="json"),
                )
                if report.passed:
                    state = self._update_series_state(
                        episode, bible, diagnosis, plan, previous_state
                    )
                    return EpisodePlanningBundle(
                        diagnosis=diagnosis,
                        plan=plan,
                        quality_report=report,
                        updated_series_state=state,
                    )
                raise ValueError(report.model_dump_json())
            except (ValidationError, ValueError) as error:
                validation_path = draft_root / f"draft_{draft_number:02d}_validation.json"
                if not validation_path.exists():
                    atomic_write_json(
                        validation_path,
                        {
                            "passed": False,
                            "stage": "deterministic_validation",
                            "errors": _validation_feedback(error),
                        },
                    )
                if revision >= self.settings.planner_max_revisions:
                    break
                repair = {
                    "revision": revision + 1,
                    "previous_response": data,
                    "validation_errors": _validation_feedback(error),
                }
        detail = last_report.model_dump_json() if last_report else json.dumps(
            repair or {}, ensure_ascii=False
        )
        raise ValueError(f"script quality gate remained invalid: {detail}")


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
        self.creative_profile = settings.creative_profile

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
                "performance_plan_required": True,
                "camera_plan_required": True,
                "camera_mode_default": "locked",
                "camera_move_requires_motivation": True,
                "one_camera_trajectory_per_shot": True,
                "dialogue_action_axis_locked": True,
                "reference_only_anchors_identity_costume_environment_style": True,
            },
        }
        normalizer = object.__new__(OpenAICompatiblePlanner)
        plan = _bounded_validate(
            "plan_episode",
            self.max_revisions,
            lambda repair: self._invoke(
                "plan_episode",
                {**payload, **({"repair": repair} if repair else {})},
            ),
            lambda data: normalizer._validate_episode_data(data, episode, bible),
        )
        return apply_creative_direction(
            plan,
            deterministic_chapter_diagnosis(episode),
            bible,
            profile=self.creative_profile,
        )

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        common = {
            "contract": "novel-manga-planner/v4",
            "novel_id": novel.novel_id,
            "episode": episode.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "previous_state": previous_state.model_dump(mode="json") if previous_state else {},
            "chapter_only": True,
            "future_chapters_allowed": False,
            "creative_profile": self.creative_profile,
        }
        diagnosis = _bounded_validate(
            "diagnose_episode",
            self.max_revisions,
            lambda repair: self._invoke(
                "diagnose_episode",
                {
                    **common,
                    "schema": ChapterDiagnosis.model_json_schema(),
                    **({"repair": repair} if repair else {}),
                },
            ),
            lambda data: validate_chapter_diagnosis(
                ChapterDiagnosis.model_validate(data), episode, bible
            ),
        )
        normalizer = object.__new__(OpenAICompatiblePlanner)
        showrunner = (
            _bounded_validate(
                "plan_showrunner",
                self.max_revisions,
                lambda repair: self._invoke(
                    "plan_showrunner",
                    {
                        **common,
                        "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                        "schema": ShowrunnerPlan.model_json_schema(),
                        "requirements": {
                            "planning_mode": "planner",
                            "shot_indexes_must_be_empty": True,
                            "source_grounded_retention": True,
                            "information_states_required": True,
                            "character_state_deltas_for_diagnosed_changes": True,
                        },
                        **({"repair": repair} if repair else {}),
                    },
                ),
                lambda data: normalizer._validate_showrunner_data(
                    data, episode, diagnosis, bible
                ),
            )
            if self.creative_profile == SHORT_DRAMA_PROFILE
            else None
        )
        repair: dict | None = None
        last_report: ScriptQualityReport | None = None
        for revision in range(self.max_revisions + 1):
            data = self._invoke(
                "plan_episode",
                {
                    **common,
                    "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                    "showrunner_plan": (
                        showrunner.model_dump(mode="json") if showrunner else {}
                    ),
                    "schema": EpisodePlan.model_json_schema(),
                    "requirements": {
                        "all_critical_events_mapped": True,
                        "adaptation_ledger_required": True,
                        "causal_chain_complete": True,
                        "creative_profile": self.creative_profile,
                        "source_grounded_result_first_cold_open": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "cold_open_must_be_replayed_after_causes": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "narration_budget_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "supplied_showrunner_plan_required_unchanged": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "retention_beats_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "information_states_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "character_state_deltas_for_diagnosed_changes": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "shot_intent_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "triggered_audio_beats_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "ending_at_current_chapter_boundary": True,
                        "one_visible_speaker_per_turn": True,
                        "performance_plan_required": True,
                        "camera_plan_required": True,
                        "camera_mode_default": "locked",
                        "camera_move_requires_motivation": True,
                        "one_camera_trajectory_per_shot": True,
                        "dialogue_action_axis_locked": True,
                    },
                    **({"repair": repair} if repair else {}),
                },
            )
            try:
                plan = normalizer._validate_episode_data(data, episode, bible)
                if showrunner is not None:
                    plan = plan.model_copy(update={"showrunner_plan": showrunner})
                plan = apply_creative_direction(
                    plan,
                    diagnosis,
                    bible,
                    profile=self.creative_profile,
                )
                plan = normalize_chronological_plan(plan, diagnosis, episode)
                deterministic = evaluate_script_quality(
                    plan, diagnosis, episode, previous_state=previous_state
                )
                if not deterministic.passed:
                    raise ValueError(deterministic.model_dump_json())
                qualitative = _bounded_validate(
                    "review_episode",
                    self.max_revisions,
                    lambda review_repair: self._invoke(
                        "review_episode",
                        {
                            **common,
                            "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                            "episode_plan": plan.model_dump(mode="json"),
                            "schema": ScriptQualityReport.model_json_schema(),
                            **({"repair": review_repair} if review_repair else {}),
                        },
                    ),
                    ScriptQualityReport.model_validate,
                )
                report = evaluate_script_quality(
                    plan,
                    diagnosis,
                    episode,
                    qualitative=qualitative,
                    previous_state=previous_state,
                )
                last_report = report
                if not report.passed:
                    raise ValueError(report.model_dump_json())
                state = _bounded_validate(
                    "update_series_state",
                    self.max_revisions,
                    lambda state_repair: self._invoke(
                        "update_series_state",
                        {
                            **common,
                            "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                            "episode_plan": plan.model_dump(mode="json"),
                            "schema": SeriesState.model_json_schema(),
                            **({"repair": state_repair} if state_repair else {}),
                        },
                    ),
                    lambda value: validate_series_state(
                        SeriesState.model_validate(value), episode, previous_state
                    ),
                )
                return EpisodePlanningBundle(
                    diagnosis=diagnosis,
                    plan=plan,
                    quality_report=report,
                    updated_series_state=state,
                )
            except (ValidationError, ValueError) as error:
                if revision >= self.max_revisions:
                    break
                repair = {
                    "revision": revision + 1,
                    "previous_response": data,
                    "validation_errors": _validation_feedback(error),
                }
        detail = last_report.model_dump_json() if last_report else json.dumps(
            repair or {}, ensure_ascii=False
        )
        raise ValueError(f"command planner script quality gate remained invalid: {detail}")
