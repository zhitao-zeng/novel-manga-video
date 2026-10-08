---
name: h3-request-agent
description: Draft, inspect, and repair final MiniMax H3 requests from approved structured shots, original source evidence, and preselected picture/audio references in an isolated agent workspace.
---

# Structured shots to a final H3 request

Own the request's English visual direction, performance, physical sound, source/reference consistency, and supported corrections together. Work from structured shots; do not translate or reparse a previous Chinese prompt. The operator supplies final picture/audio ordering before writing begins. This skill prepares requests; the caller owns generation, retries, production publication, and video acceptance.

Read `input/manifest.json`, then each listed case file. A case contains approved shot starts, events, results, framing, lighting, actions, wearing bindings, exact dialogue, source excerpts, selected references, and protected identifiers. Inspect the actual reference pictures with the image-reading tool. A filename or an old portrait description is not evidence of what the picture contains. Shared image paths need not be reopened for every case.

Write one `output/<case_id>.json` per case. For a usable request, return:

- `case_id`: the input identifier.
- `status`: `ready`.
- `prompt`: the complete English H3 request.
- `review`: concise `reference_observations`, `source_checks`, and `corrections` lists, describing actual evidence and any changes made during self-review.
- `problems`: an empty list.

When the approved shots/source/references have a material contradiction that cannot be expressed faithfully, return `status: blocked`, an empty `prompt`, and specific `problems`. Explain the required source or asset correction. Do not invent an event, a missing asset, or a new character to force a ready result.

## Authority and factual decisions

The original source constrains facts. The approved shot determines this take's event, visible participants, wearing state, camera, and order; do not add every source event to this take. The actual pictures supply stable identity and design, not their photographed pose, facial expression, default helmet state, or the number of people to render. A turnaround's multiple views show one identity, not several actors. A setting picture supplies architecture/terrain; use the shot's story time, light, people, and movable objects.

Use an explicit `in_frame` list when the original shot supplies one, including an explicitly empty list. Legacy `characters` and `listeners` are cast/role metadata, not a substitute hard visibility list. If `in_frame` is absent, interpret the original visual_prompt, motion_prompt, end_state and camera together, retaining listener information. A wearing binding does not by itself place a person on camera. Do not silently pick a new frame to resolve a genuine contradiction.

Keep each character, anonymous person/animal, object, and wearable distinct. Do not assign a model name or identical design to an unspecified separate object just because a character reference contains one design; use only the appearance the source and approved shot actually establish. Worn armour belongs to its wearer; a separate empty copy exists only if the shot explicitly requires it. Respect the fixed reference table and its picture/audio/subject/speaker identifiers. An off-screen speaker does not gain a visible body. A missing voice reference does not remove their dialogue: preserve the utterance with its assigned speaker and simply omit an unavailable audio-timbre association.

Actions and event prose may describe the same event twice. Express that event once, preserving its owner, target, cause, and result. Do not independently prepend the action list to the prose. Split parts describe their current scope, not a reason to replay the complete source event. Treat supplied corrections as replacements for conflicting wording, not an extra paragraph beside the old instruction. Preserve unaffected facts and dialogue.

## Final H3 format

Use these six section headings, each alone on its line, in this order:

1. `subject_definitions:`
2. `summary:`
3. `retention_analysis:`
4. `detailed_description:`
5. `overall_soundscape:`
6. `non_diegetic_music:`

Begin `summary` with `[reference generation]`, or `[reference generation + audio reference]` when the selected table contains audio.

Define the supplied `<Subject N>` associations with the exact `<Picture N>` references. Describe people as individuals, settings as settings, and props as objects. Retention uses `fully_preserved` for the referenced person/setting/prop design and `reference` for voice timbre; preserve explicit wearing/independent-copy relationships. Do not make the portrait's expression or pose a permanent identity constraint. Define `<Audio N>` only for an audio in the selected table, associating it with the supplied speaker `(Sx)` and subject when there is one. The audio supplies vocal timbre, not its recorded words.

In `detailed_description`, write one numbered `[Shot N]` block for every supplied shot, in order. Use its supplied `Planned duration: ... seconds.` and `At ...` timing when provided. A total take length does not supply individual shot lengths: if a shot has no approved timing clause, do not divide the total, invent exact seconds or add a Planned duration/At numeric schedule. Keep shot order and authored relative event/utterance timing. State the start/framing, unfold the planned event once, and describe a meaningful result without repeating the event. Put each protected dialogue clause from the case verbatim where it occurs; combine the supplied emotion and dramatic intention into one nearby English performance sentence. Do not announce finishing speech before the utterance or add generic waiting to fill the take.

All prose is English. Chinese appears only inside `<d>[Chinese] ...</d>` for the exact bound words. Do not add utterances, join different owners, turn private thought into audible scene speech, or change a voiceover into lip-synced dialogue. Private thought is heard by the audience only; other characters do not respond to it. A silent-visual take contains no generated speech.

Describe only visible motion and acting in the shot paragraphs; do not repeat a thud, creak, engine sound, ambience, or other physical audio there. Put authored physical sound only in `overall_soundscape`, with shot number and onset/stop relative to the action; put continuous ambience there once. A vocal pause belongs beside its utterance, not an invented opening delay. Removing an effect also removes its corresponding sound. Add no unspecified electrical arcs, effects, laughter, crying, shouting, music, or people. Use `None.` when no physical sound or music is planned. Preserve the specified animation/photographic medium and authored camera; avoid adding decorative style or a conflicting generic camera rule.

## Inspect and correct your own draft

Compare the final request against structured shots, original source and actual references: ownership and body count, wearing versus empty objects, entry/exit order, start/event/result continuity, camera/light, dialogue owner/order/delivery, and physical sound. Correct demonstrated contradictions by replacing the responsible passage; do not treat a missing descriptive phrase as proof of a generation cause. State uncertainty when evidence cannot decide it. Native format success is not proof that a video will implement the request.

Run `python3 .claude/skills/h3-request-agent/scripts/check_request.py input/manifest.json output/<case_id>.json`. This checker protects deterministic format, dialogue, timing, and reference associations; your semantic/image review remains your responsibility. If it rejects the draft, fix its specific errors and reread the affected facts. Use at most two focused correction rounds per case, then return blocked with the unresolved reasons. Save the final review in the case output, including any correction you actually made.

Only read supplied `/work` evidence and write `/work/output`. Do not inspect credentials, call image/video generation or production APIs, install packages, change reference selection/order or budgets, or reach production paths. Do not call another model to redo the compiler/reviewer pipeline: use your own reasoning and the supplied deterministic check. If rendered takes are later supplied, inspect those actual frames/audio before choosing a supported correction; a request/reference pass alone cannot establish that the rendered shot is correct.
