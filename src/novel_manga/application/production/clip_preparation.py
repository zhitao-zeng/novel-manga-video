"""Prepare the exact first-pass request before admitting it to the video queue."""
from types import SimpleNamespace
from novel_manga.media.context import RenderContext

from novel_manga.util import read_json
from novel_manga.media.generation import build_request
from novel_manga.application.profiles import h3_prompt_outdated
from novel_manga.application.preparation.readiness import render_risks, risk_acceptances, worn_reference_pairs
from novel_manga.application.preparation.request_check import before_generation
from novel_manga.application.preparation.wearer_cards import check_references
from novel_manga.application.repair import history
from novel_manga.story.h3 import request_issues


def preflight(directory, clip):
    # The rendering CLI resolves its novel directory; use the same reference paths/cache key.
    directory = directory.resolve()
    notes = read_json(directory/'review_feedback.json', {})
    cid = clip['clip_id']
    if h3_prompt_outdated(clip, str(notes.get(cid) or ''), strict=True) or clip.get('prompt_h3_skip'):
        raise ValueError('H3 request is missing or stale')
    if (history.generation_counts(directory).get(cid, 0) >= history.generation_limit(directory, cid)
            and not history.extra_take_ready(directory, cid)):
        raise ValueError('effective clip retry budget used; requires a validated input change and existing authorization')
    ctx = RenderContext(novel_dir=directory.parent, work=directory/'work', feedback=notes,
                          settings=SimpleNamespace(local_h3_base_url='prepared'))
    request, _, _, _ = build_request(ctx, clip, 1)
    problems = request_issues({**clip, 'prompt_h3': request['prompt']})
    if problems:
        raise ValueError('H3 final request invalid: ' + '; '.join(problems))
    script = read_json(directory/'chapter_script.json', {})
    check_references(directory.parent, worn_reference_pairs({'clips': [clip]}, script))
    accepted = risk_acceptances(directory, {'clips': [clip]})[cid]
    blocking, _ = render_risks(clip, directory.parent, accepted, script=script)
    if blocking:
        raise ValueError('; '.join(blocking))
    before_generation(ctx, clip, request)
