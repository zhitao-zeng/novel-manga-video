"""Shared scoped source checks; retains novel field/protocol contracts."""
import json
from novel_manga.application.agents.episode.arguments import UnsupportedInput, SourceCheckIncomplete
from novel_manga.review.contracts import WATCHABLE_CRITERIA

def source_check(source, quote, shots, phases, locations=None, scoped_shots=None, *, characters=None):
    from novel_manga.llm.client import ask_json, obj
    from novel_manga.review.endpoints import judge_settings
    from novel_manga.story.fields import turn_field
    from novel_manga.planning.constants import DELIVERY_MODES
    turn_fields = turn_field([], [], DELIVERY_MODES)['properties']
    turn_encoding = {key: turn_fields[key] for key in ('delivery_mode', 'inner_monologue')}
    fields = sorted({f'shots[{p["index"]}].{key}' for p in shots for key in p if key != 'index'}
                    | {f'phases.{name}' for name in phases}
                    | {f'locations.{name}' for name in locations or {}}
                    | {f'characters.{name}' for name in characters or {}}) or ['existing_wearing']
    def judge(parts, schema, **kwargs):
        try:
            return ask_json(parts, schema, **kwargs)
        except (ValueError, KeyError, TypeError) as error:
            raise SourceCheckIncomplete('source review protocol incomplete',
                {'consistent': None, 'complete': False, 'error': str(error)}) from error
    prompt = ('核对原文与一份局部输入修复候选。判断修改后的入镜人物、群演、听者、地点、光线、外貌、穿戴和姿态衔接是否有原文依据，'
              'shots是稀疏补丁；affected_shots是结果上下文，其中出现turns不表示本次修改了对白。'
              '冲突须归属scope_fields中的实际候选字段，不能把上下文未改字段冒充修订。'
              '保留既定镜头和时长，关键事件及归属须与原文一致；普通表演衔接按共享可看口径判断，不因未逐字写出就视为新增剧情。speaker_map只能纠正原文能明确核实的旧说话人错误；无证据不能换说话人。'
              '只有shots显式包含turn_texts时，允许对超出发声时长的改编对白缩写；必须保留关键问答、决定、否定、条件、因果、数字含义及说话人，不能只因非逐字引用而拒绝。其他对白不得更改。'
              '若shots显式包含turns，则本次申请是语义修复：可按原文纠正、删除错误改编对白，补回必要问答，纠正说话者及内心/公开发声方式。'
              '必须保留原文关键事实、人物知识边界、立场和因果；不能把猜测变成事实，不能让旁人听见原文中的内心想法。'
              'turn_encoding给出本项目实际字段契约：offscreen_dialogue表示声音不由画内人物对口型发出，本身不代表说话者在当前现场；'
              '必须结合inner_monologue判断是否为心声，不要求另一个未定义的delivery_mode。'
              '不得用旧错误设定否定原文明确事实，也不得凭引用真实就默认修改正确。'
              '允许纠正旧稿错误的演员绑定和声景发声暗示、登记原文已有但素材库缺失的人物；不得虚构原文没有的人物或事件。'
              'characters是缺失人物素材设计；群体必须保留原文人数和各自身份，不能用无关主角替代，不把群体换装误当单人的状态。'
              '人物与穿戴归属必须对应；常规美术设计可合理补足，不能改变明确的室内外位置。'
              'locations 是无人场景参考资产的说明，不是成片镜头；参考图无人不代表成片无人，成片入镜人物以 affected_shots 为准。'
              '资产名称是用于区分地点的标签，不要求逐字出现在原文；窗户、材质等常规美术细节只在与明确原文冲突时否定。'
              '仅核对候选实际修改的字段及其直接依赖，不因未修改的旧稿其他问题否决这项局部修复。'
              '原文未逐字描述的正常衣料声、呼吸声、站位、长袍长度、发型等美术实现不是新增剧情；'
              '只拦截明确事实矛盾，不因“未明确说到”推定矛盾。称谓或同义地点标签也不等于换了地点。'
              '不支持或与原文相反的修改给出具体problems；所有修改有依据才consistent=true。'
              'problems仅填写确定的事实矛盾，不能填写“有依据”“符合要求”等肯定说明；没有矛盾必须problems=[]。'
              + WATCHABLE_CRITERIA + '\n'
              + json.dumps({'source': source, 'source_quote': quote, 'shots': shots, 'scope_fields': fields,
                            'phases': phases, 'locations': locations or {}, 'characters': characters or {},
                            'affected_shots': scoped_shots or [], 'turn_encoding': turn_encoding}, ensure_ascii=False))
    result = judge([{'type': 'text', 'text': prompt}],
        obj({'consistent': {'type': 'boolean'}, 'problems': {'type': 'array', 'items': {'type': 'string'}}}),
        name='episode_input_source', max_tokens=1200, timeout=180, settings=judge_settings())
    if (not isinstance(result, dict) or type(result.get('consistent')) is not bool
            or not isinstance(result.get('problems'), list)
            or any(not isinstance(p, str) for p in result['problems'])):
        raise SourceCheckIncomplete('source review protocol incomplete',
            {'consistent': None, 'complete': False, 'raw_answer': result})
    if result.get('consistent') is not True or result.get('problems'):
        # A judge may put support for the candidate in its problems array, or
        # contradict its own boolean. Confirm actual defects once, not the prose
        # shape, against the original source and the same scoped candidate.
        confirmation = judge([{'type': 'text', 'text': prompt + '\n'
            '现在独立核实这份初审结论，初审文字不是事实。只返回实际成立的原文与候选冲突。'
            '剔除肯定说明、合法缩写、旧稿未改字段和无证据推断；不能因初审不通过而维持拒绝。'
            '每项conflicts必须给field、逐字source_quote和具体problem，field只能选scope_fields里的实际修订字段。'
            '如果全部有据或只有肯定说明，conflicts=[]；无确定矛盾不补造问题。\n'
            + json.dumps({'initial': result}, ensure_ascii=False)}],
            obj({'conflicts': {'type': 'array', 'items': obj({
                'field': {'type': 'string', 'enum': fields},
                'source_quote': {'type': 'string'}, 'problem': {'type': 'string'}})}}),
            name='episode_input_source_confirmation', max_tokens=1200, timeout=180, settings=judge_settings())
        conflicts = confirmation.get('conflicts') if isinstance(confirmation, dict) else None
        if (not isinstance(conflicts, list) or any(not isinstance(c, dict) or c.get('field') not in fields
                or not isinstance(c.get('source_quote'), str) or not isinstance(c.get('problem'), str) for c in conflicts)):
            raise SourceCheckIncomplete('source confirmation is incomplete or outside the proposed fields',
                {'consistent': None, 'complete': False, 'initial': result, 'confirmation': confirmation})
        if any(not c['source_quote'].strip() or c['source_quote'] not in source
               or not c['problem'].strip() for c in conflicts):
            raise SourceCheckIncomplete('input source confirmation lacks a verbatim source quote or concrete conflict',
                {'consistent': None, 'complete': False, 'initial': result, 'confirmation': confirmation})
        result = {'consistent': not conflicts, 'problems': [c['problem'] for c in conflicts],
                  'initial': result, 'confirmation': confirmation}
        if conflicts:
            error = UnsupportedInput('input correction lacks source support: ' + '; '.join(result['problems']))
            error.review = result
            raise error
    return result
