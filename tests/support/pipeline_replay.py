from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""Replay 25 saved clip plans without model calls or production files."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import novel_manga.application.rendering.h3 as translation
from novel_manga.story import h3
from novel_manga.media import cache, generation
from novel_manga.repair.policy import diagnosed_decision


def clip_requests(root: Path) -> dict:
    cases = json.loads((Path(__file__).parents[1] / 'fixtures/pipeline_25_clips.json').read_text())
    before = copy.deepcopy(cases)
    output = {}
    for case in cases:
        for original in case['clips']:
            for local in (False, True):
                clip = copy.deepcopy(original)
                ctx = RenderContext(novel_dir=root, feedback={}, settings=SimpleNamespace(local_h3_base_url='pool' if local else None), options=RenderOptions(voice_budget=15))
                for ref in clip['references']:
                    if ref['role'] != 'voice':
                        path = root / ref['path']
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b'fixed reference content')
                stages = h3.stages_of(clip['prompt'])
                # Translation is fixed: this replay checks binding and composition, not model quality.
                translated = [f'The camera shows stage {i}. ' + ' '.join(
                    f'<DIALOGUE_{j}>' for j in range(1,1+sum(r['stage']==i for r in clip.get('dialogue_bindings',[]))))
                    for i in range(1,len(stages)+1)]
                clip['prompt_h3'] = h3.compose(clip, translated, stages)
                request, refs, digests, retry = generation.build_request(ctx, clip, 1)
                result = {'stages': stages, 'request': request, 'retry': retry,
                          'cache_match': cache.request_matches(ctx, clip, request, refs, digests),
                          'bindings': clip['dialogue_bindings']}
                key = f"{case['book']}/{case['chapter']}/{clip['clip_id']}/{'h3' if local else 'sd'}"
                output[key] = json.loads(json.dumps(result, ensure_ascii=False).replace(str(root), '<root>'))
    assert cases == before
    output['repair_routes'] = {cause: [diagnosed_decision({'cause': cause}, repeated).action
                                      for repeated in (False, True)]
                               for cause in ('generation_mismatch', 'source_attribution', 'request_mismatch')}
    output['translation_contract'] = {'shots': translation.ASK, 'input_rules': translation.INPUT_RULES, 'schema': translation.SCHEMA}
    return output
