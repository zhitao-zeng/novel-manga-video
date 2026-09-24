"""The outline promise is about meaning and who can hear it, not shared characters."""
import pytest

from novel_manga.application.planning import retention
from novel_manga.planning.issues import PlanningCode
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.planning.context import PlannerContext
from novel_manga.models.bible import StoryBible, Character


def test_review_gets_whole_section_and_speaker_scene_evidence(monkeypatch):
    section = '席勒在诊室答应免费同行；后来第九个笑点是巴士在天上飞。'
    seen = {}

    def judge(parts, schema, **kwargs):
        seen['input'] = parts[0]['text']
        return {'verdicts': [
            {'promise': '免费同行', 'status': 'changed', 'reason': '当面对托尼说了不免费，意思反转'},
            {'promise': '巴士笑点', 'status': 'missing', 'reason': '巴士一段没有可听的收尾'},
        ]}

    monkeypatch.setattr(retention, 'ask_json', judge)
    shots = [{'label': '诊室1', 'location': '诊室', 'event': '席勒面对托尼',
              'turns': [{'speaker_name': '席勒', 'delivery_mode': 'visible_dialogue',
                         'text': '好吧，这次免费是不可能的。'}]},
             {'label': '公交1', 'location': '公交站', 'event': '巴士离地', 'turns': []}]
    verdict, issues = retention.review(section, shots, '原文里席勒答应免费同行，后来开玩笑说巴士会飞。')
    assert section in seen['input'] and '公交站' in seen['input']
    assert 'visible_dialogue' in seen['input'] and 'speaker_name' in seen['input']
    assert len(verdict['verdicts']) == len(issues) == 2
    assert all(issue.code == PlanningCode.RETAINED_LINE_LOST for issue in issues)
    assert '反转' in issues[0].message


def test_review_requires_an_explicit_verdict_for_a_nonempty_promise(monkeypatch):
    monkeypatch.setattr(retention, 'ask_json', lambda *a, **k: {'verdicts': []})
    with pytest.raises(ValueError, match='没有逐项结论'):
        retention.review('席勒要让托尼知道自己答应免费同行。', [], '')


def test_review_can_accept_a_paraphrase_and_preserve_a_saved_verdict():
    verdict = {'section': '免费同行', 'verdicts': [
        {'promise': '免费同行', 'status': 'delivered', 'reason': '席勒当面对托尼说今天不收钱，并同意一起过去'}]}
    assert retention.interpret(verdict, '免费同行') == []
    with pytest.raises(ValueError, match='不属于当前提纲'):
        retention.interpret(verdict, '改过的提纲')


def test_default_shots_without_scene_ids_still_detect_a_copied_new_location():
    quote = '两人在诊所门口谈话，随后来到公交站等车。'
    shared = {'segment_id': 'seg_1', 'source_quote': quote, 'camera': '', 'light': '',
              'sfx': '', 'shot_scale': '中景', 'turns': [], 'in_frame': [], 'actions': [], 'extras': []}
    clinic = {**shared, 'start_state': '低矮的诊所里，两人相对而坐',
              'event': '席勒解释自己的诊所为何如此破旧，托尼站在门旁看向他',
              'end_state': '诊所内两人相对，托尼仍靠着门旁的破旧家具'}
    bus = {**shared, 'start_state': clinic['event'], 'event': '两人出门等车', 'end_state': '公交车开来'}
    raw = {'video_title': '公交', 'hook': '', 'summary': '', 'skipped_segments': [],
           'clips': [{'clip_id': 'clip_1', 'location': '诊所', 'characters': [], 'avoid': '', 'stages': [clinic]},
                     {'clip_id': 'clip_2', 'location': '公交站', 'characters': [], 'avoid': '', 'stages': [bus]}]}
    bible = StoryBible(novel_title='t', genre='generic', visual_style='3d', palette='', style_fingerprint='f',
                       characters=[Character(name='席勒', role='主角', appearance='黑发', wardrobe='黑衣')],
                       locations=['诊所：低矮诊室', '公交站：街边站牌'])
    result = validate_and_normalize(raw, [{'segment_id': 'seg_1', 'text': quote}], bible,
                                    {'诊所': bible.locations[0], '公交站': bible.locations[1]},
                                    quote, ctx=PlannerContext())
    assert PlanningCode.SCENE_OPENING_COPIED in {issue.code for issue in result.issues}


def test_same_summoning_action_cannot_be_replayed_for_the_answer():
    quote = '托尼打响指召来第二套机甲，席勒问他要不要坐进去，托尼回答。'
    common = {'segment_id': 'seg_1', 'source_quote': quote, 'start_state': '托尼和席勒在诊室',
              'event': '托尼打响指召来第二套机甲', 'end_state': '第二套机甲落在席勒面前',
              'camera': '平视', 'light': '台灯', 'sfx': '喷气声', 'shot_scale': '中景',
              'in_frame': ['托尼', '席勒'], 'actions': [{'actor': '托尼', 'action': '召来', 'target': '第二套机甲'}],
              'extras': [], 'props': ['第二套机甲']}
    def stage(speaker, words):
        return {**common, 'turns': [{'speaker_name': speaker, 'delivery_mode': 'visible_dialogue',
                                    'text': words, 'emotion': '平静', 'chat_target': ''}]}
    raw = {'clips': [{'clip_id': 'c', 'location': '诊室', 'characters': ['托尼', '席勒'], 'avoid': '',
                      'stages': [stage('席勒', '让我坐这个？'), stage('托尼', '不然呢？')]}],
           'skipped_segments': []}
    bible = StoryBible(novel_title='t', genre='generic', visual_style='2d', palette='', style_fingerprint='f',
                       characters=[Character(name=n, role='主角', appearance='黑发', wardrobe='外套') for n in ('托尼', '席勒')],
                       locations=['诊室：桌边'])
    result = validate_and_normalize(raw, [{'segment_id': 'seg_1', 'text': quote}], bible,
                                    {'诊室': bible.locations[0]}, quote, ctx=PlannerContext())
    assert PlanningCode.DUPLICATED_STAGE_ACTION in {issue.code for issue in result.issues}
