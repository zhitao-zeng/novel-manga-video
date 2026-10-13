"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations


from novel_manga.util import read_json


def initial_prompt(ctx):
    drafted = ((ctx.work_dir/'output/episode.json').is_file()
               and read_json(ctx.run_dir/'source-draft-call.json', {}).get('status') == 'candidate')
    common = (
        'Continue the saved draft and supplied checks; consult the relevant episode-production contract sections. ' if drafted else
        'Use the installed episode-production skill to execute this episode. '
    ) + (
        'Read input/current-task.json for current targets, exact permissions and evidence_file links; '
        'input/current-status.json retains the full state. Use the installed repair_playbook '
        'for matching failures and related_history evidence files for actual prior decisions. '
        'Current source, selected references and host results take precedence over old cases. '
        'Read the relevant output contract before writing files. Submit those files through '
        '/work/tools/episode.py; a JSON reply does not execute an operation. '
        'A yielded job is already submitted: end this turn and resume from the returned result. '
        'Preserve existing work; use review and finish to report the actual accepted or unresolved outcome.\n'
    )
    if ctx.input_mode == 'source' and (ctx.work_dir/'input/reading/character-phases.json').is_file():
        common += ('Use input/reading/character-phases.json for current chapter appearance and clothing; '
                   'these explicit phase fields override the base Bible look. Inspect the corresponding '
                   'selected cards rather than reverting a current phase to its older base design.\n')
    if ctx.input_mode == 'script':
        from .local_preparation import active
        if active(ctx):
            return common + (
                'This is an existing provisional screenplay with a local preparation failure. '
                'Read input/preparation/exception.json and input/latest-preparation.json when present. '
                'Keep its cuts, durations, dialogue and valid choices. Do not write a new episode.json. '
                'The host stages the existing inputs without video; use apply action=input for '
                'source-backed speaker, scene, character/reference and wearing corrections, '
                'action=edit for one invalid English request, or accept_risk for an explicitly '
                'offered reference uncertainty. Input may build needed scene/character cards. '
                'render rechecks the current source, fills missing references and translates only '
                'missing/invalid requests before normal H3 and media gates. Old preliminary findings '
                'are evidence, not proof of a current defect. Use current results and finish normally.\n')
        return common + (
            'This is script input mode. The existing approved chapter_script.json, clip_plan.json '
            'and selected assets are authoritative. Preserve every shot, story event, exact dialogue '
            'and speaker, timing, camera, reference selection/order and source binding. '
            'Keep these accepted choices except for a concrete source/input defect corrected through the input tool. '
            'Do not replan the whole episode or impose a new episode length. '
            'For a confirmed source/input conflict, use apply with action=input, a verbatim source_quote '
            'and supported cast/speaker/scene/appearance/wearing/sound corrections. '
            'shots can correct in_frame/extras/listeners/actions, event/start_state/end_state, '
            'speaker_map and sfx. characters requests a missing source actor card after adoption too. '
            'The host checks source support, builds scoped cards and recompiles dependent clips retaining cuts and words. '
            'output/requests.json is already seeded with the existing final requests. '
            'Read manifest.request_draft: reusable lists structurally valid requests and needs_work '
            'names missing or invalid clips. Preserve reusable text unless you find a concrete '
            'source, image or request defect. Edit only those defects and missing requests; '
            'style_warnings alone do not require a rewrite. Use the host check diagnostics '
            'instead of writing duplicate validators or shell probes. Perform your semantic/image '
            'review, and execute the existing episode through the supplied tools. '
            'Keep repairs within the existing protected request contract.\n'
        )
    if (ctx.run_dir/'prepared.json').is_file():
        return common + (
            'This source episode already has an adopted plan. You are handling an exception '
            'in that existing episode. Read the current status and '
            'input/latest-render.json and input/latest-review.json when present; they describe '
            'the current requests and actual takes. Do not redo initial source drafting or '
            'treat an old source-draft-check as a new failure. Preserve existing media and its '
            'physical-take budget. If can_adopt_draft is false, use apply for remaining issues '
            'instead of rewriting the screenplay or resubmitting the initial draft. '
            'Use source evidence for cast/speaker/appearance/wardrobe/scene/sound corrections. '
            'Input can correct references and request missing source actor cards with characters. '
            'Use shots.in_frame/extras/listeners/actions, speaker_map and sfx as needed; '
            'unnamed crowds remain extras unless an actual speech owner requires a card. '
            'accept_risk can explicitly acknowledge a '
            'current wearer-card or scene-people uncertainty for one clip before rendering; '
            'it does not pass any media gate. keep is only for an actual existing video.\n'
        )
    if read_json(ctx.work_dir/'input/manifest.json', {}).get('preparation_exception'):
        return common + (
            'This unfilmed chapter failed normal planning or preparation. Read '
            'input/preparation/exception.json and its listed original candidate and audit files. '
            'The failed screenplay is provisional, not an approved frozen script. Diagnose and '
            'repair the recorded cause, preserving its valid story choices instead of repeating '
            'the normal writer. Use input/reading/locations.json when present: these names and '
            'chapter memberships come from the book reading; their descriptions are evidence, '
            'not ready-made empty-scene prompts. A missing picture is work to perform: write '
            'the corrected provisional output/episode.json and request source-backed missing '
            'characters/locations through apply action=input using the draft-assets contract. '
            'Do not substitute an unrelated available scene or remove a required source actor '
            'to avoid creating a reference. Use the existing source output contract, check and '
            'render; the host retains source, reference, request and media acceptance gates.\n')
    if (ctx.run_dir/'source-draft-call.json').is_file():
        return common + (
            'This is source input mode with a normal production draft already attempted. '
            'You are handling an exception. Read input/source-draft-check.json when present '
            'and output/episode.json; correct the reported issues while keeping valid story '
            'choices. The draft is provisional: you may repair its screenplay, references, '
            'cutting and H3 bodies against the complete original source and actual assets. '
            'input/source-draft-raw.json, when present, preserves the normal writer response '
            'as ordered clip/shot beats; reuse its valid choices if host binding failed. '
            'If the model call produced no usable draft, author it from the supplied source. '
            'Preserve essential causes, dialogue ownership and the ending within the manifest '
            'duration target. Use the existing source output contract and execute the tools.\n'
        )
    target = ctx.target_seconds
    duration = (f'{target[0]:g}–{target[1]:g} seconds' if target else
                'the duration specified in input/manifest.json')
    return common + (
        'This is source input mode. Read the complete original chapter. Create the episode '
        'screenplay, ordered shots and English shot directions yourself from that source and '
        f'the supplied assets. Aim for {duration}, with each clip no longer than '
        f'{ctx.clip_seconds_max:g} seconds. Preserve source-to-shot evidence and meaningful '
        'adaptation choices. Prefer clips[].h3={shots:[English body with <DIALOGUE_n> slots],soundscape:English}. '
        'The host fills reference definitions, dialogue, voice IDs, durations and cuts. '
        'Write output/episode.json using the output contract, self-review '
        'the complete episode, and execute it through the supplied tools.\n'
    )


def continuation_prompt():
    return (
        'Continue this same saved episode session and existing workflow. The previous CLI turn '
        'ended and the host operation has now settled. The current host state and operation '
        'result are supplied below; continue directly from them. Read the saved evidence '
        'when making a creative or repair decision. Render already exports its independent '
        'review evidence; call review again only for missing/stale evidence or when full media context is needed. '
        'Current repair API: an edit may use h3={shots:[English bodies with <DIALOGUE_n> slots], '
        'soundscape:English} instead of prompt_h3. Prefer this short form; the host preserves '
        'reference bindings, original speech, durations, cuts and music. Read only the evidence '
        'needed for the current unresolved clips. Use attention_clips for repair decisions; '
        'retained_clips need no decision or repeated inspection. '
        'For episode_technical_issues, use the inspection targets to locate and repair only responsible clips; '
        'do not submit keep decisions merely to cover all technical inspection targets. '
        'Batch independent decisions in one apply. A clip with no remaining take allowance '
        'does not cancel eligible repairs for other clips. '
        'Current remaining_input_attempts, remaining_rechecks, conditional_extra_takes and input_repairs override historical blocked messages. '
        'repair_actions lists apply operations permitted NOW, including edit/retake/input preparation for a pending extra. '
        'Submit a listed supported correction; the host handles later activation. A missing planned line can be repaired '
        'with edit or a justified retake while preserving its words; it does not require a source rewrite. '
        'A previous stopped result is not acceptance and does not revoke a current allowance. '
        'Use current repair_capabilities: submit a supported correction/resume, or identify the exact '
        'missing capability, rejected source decision, exhausted allowance or external dependency. '
        'Do not repeat the old completion narrative instead of an operation. '
        'remaining_takes includes unspent conditional extras; prepare a validated repair before those can generate. '
        'Inspect current evidence; use keep for an independent recheck when a verdict is doubtful, '
        'otherwise submit a concrete edit/input/retake. Extra generation activates only after input preflight; '
        'risk acceptance alone is not a repair plan. First run python3 /work/tools/episode.py check output/repair.json to catch '
        'argument errors in this turn without spending an input attempt or handing off to a slow tool. '
        'A historical tool_error did not spend an input attempt. Use an offered continuation or revision '
        'when available; do not repeat a missing or malformed saved decision through resume. '
        'For an already registered character state, inspect input/phases.json and its actual card; '
        'input phases.characters.<name>={reuse_asset_id:<existing phase asset ID>} reuses that saved design. '
        'Do not rewrite appearance or wardrobe merely to apply the same state to another clip. '
        'After adoption, action=input supports scoped shots.in_frame/extras/listeners/actions and '
        'event/start_state/end_state/sfx corrections, plus speaker_map from old to source-correct speech owners. '
        'characters can request a missing named visible actor or actual speech owner with appearance/wardrobe. '
        'For source-authored speech_overflow, input shots[].turn_texts may shorten each existing turn in order; '
        'the host preserves the speaker/mode and independently checks essential meaning against the source. '
        'Aim for 4–5 Chinese characters per second, never more than 6. This permission does not apply to frozen script input. '
        'A character missing from the old selected references is not prohibited when corrected shots '
        'and original source establish that identity. Existing cards use phases for changed clothing. '
        'Do not put a missing identity into phases; request characters instead. Unpictured crowds can remain extras. '
        'Before source adoption, missing assets can be requested with apply action=input, '
        'a verbatim source_quote, and characters (name to appearance/wardrobe/gender/age) '
        'or locations (name to empty-setting description). Read the returned actual images '
        'and bind their IDs in the provisional draft; asset creation does not start video. '
        'When a tool returns yield=true, end this turn so the host can execute it. '
        'Complete the remaining checks, review or supported repair within the existing budget '
        'and input authority. Run finish when accepted or when the episode must stop, and '
        'end this turn after its acceptance; the host will complete it.\n'
    )
