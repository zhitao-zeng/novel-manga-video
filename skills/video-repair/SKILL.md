---
name: video-repair
description: Diagnose a failed video clip from source, requests, images, actual takes and review history, then propose a bounded model-specific request correction.
---

## Core repair principles

- **Replace superseded logic.** When correcting timing, actions or states, rewrite
  the affected description and remove the conflicting old instruction. Do not keep
  both and append a contrary correction. Superseded clauses must be absent from
  the final candidate: a later paragraph labelled "correction" does not remove
  them, and presumed precedence between paragraphs does not resolve the defect.
  Compatible new detail is allowed; read the complete repaired request as one plan.
- **Use one chronology.** Describe an opening state, ordered actions and an ending
  state. Within a continuous scene, carry that ending state into the next cut until
  an authored action changes it. Do not replay a completed action to establish its
  result again.
- **Repair the evidenced cause.** Compare source, selected references, actual request
  and current take. Correct the earliest supported defect. A bad take does not by
  itself prove its inputs wrong; an old review claim is not current-frame evidence.
  A filename or written observation does not prove that the model received an image.
  For temporal or spatial defects, compare chronological raw/final evidence and
  neighbouring cuts using fixed world landmarks; small contact sheets can hide
  high-frequency movement, and screen left/right does not establish world seats.
- **Distinguish validation from success.** Host `check` validates its reported
  contract and permissions; it does not establish semantic consistency or a better
  film. Before submission, judge whether the complete candidate follows these
  principles. A replacement still needs actual independent acceptance.

Apply these principles even when no matching case is retrieved. Individual cases
are optional evidence for a remedy, not a prerequisite for the general rules.

## Clip repair workflow

The host already decoded the videos. When primary.final_cut is present, first inspect its supplied final-cut JPEGs and verify the reported issues against actual pictures and source_basis. Those timestamps are global seconds in the reviewed final movie and include any relevant neighbouring clip boundary; do not substitute native-take frames for final-movie continuity or blindly turn a report into a fact. Missing or inconclusive final-cut evidence requires stop with the precise missing evidence, or keep for an independently verified false positive. For native findings, start by reading the current take's JPEGs in evidence.takes[].frames; their seconds are sample times within that take. Use `python3 tools/video_frames.py take-1` or `--seconds 2.0` to locate supplied native frames. The time selector returns the nearest supplied sample, not a new exact-time extraction. Check each take's own request and ASR; reference cards are not actual generated footage. Do not use Read on MP4, install packages, probe system video libraries, or spend the repair window preparing tools. If images cannot support a decision, output stop with the precise missing evidence. Produce output/decision.json within this attempt even when blocked.

Read input/primary.json first; input/evidence.json retains the full source and history. This task diagnoses and repairs one failed clip through its earliest wrong input within a 600-second execution window. Read input/upstream.json for actual reviewed alternatives and source/audio candidates; zero video balance still permits bounded input/audio repair. Start with the current frames, actual request, selected reference and inline target/neighbouring shots with their source facts. Historical takes, whole-episode source documents, interaction/overlay files and asset profiles are available for specific unresolved contradictions, not a mandatory reading list. Do not re-read an archived copy of the current render as if it were a prior generation. Once primary evidence supports input, asset, audio, edit, retake, keep or stop, write the final output immediately; do not spend the window exhausting auxiliary files. Use the shared request_rules supplied by the production compiler. Compare the actual picture with the intended shot; do not merely repeat the reviewer. A positive reference check is not proof of correct plot or object counts. Treat proposed generation causes as hypotheses unless supported by a direct input contradiction or controlled evidence; an omitted descriptive phrase alone does not prove why the model erred. Category changes can describe the same visible fault.

Return output/decision.json with action (input, asset, audio, edit, retake, keep, stop), reason, and evidence (a list of input file paths plus concise observations). Read provider and request_field in input/primary.json. For edit write output/decision.json and output/prompt.txt together in one tool turn, then finish: the complete final H3 English request for h3, or the existing compiled Chinese request for sd25. Never transfer H3 tags or section grammar into SD. Replace conflicting text rather than appending the error description. Describe the target visible state concisely instead of listing every prior defect as a prohibition. Organize each shot as setup, all ordered bound utterances and intervening actions, and the meaningful result; preserve every turn in a continuous conversation; do not narrate finishing speech before the dialogue or add a generic hold to fill time. Correct wearing versus independent objects, visibility and physically observable states using the source and actual references; do not invent a new scene. If a source field or reference selection is wrong, use input to repair it; stop only if there is no source-supported correction. Do not disguise source/reference/audio correction as a final-English edit.

For H3, preserve the six H3 sections, subject/audio labels and their reference mappings, retention labels/modes, exact dialogue and speaker/audio bindings, shot count and timing. You may clarify identity/clothing/state descriptions in subject_definitions and retention_analysis using the reference evidence, and revise shot directions and audiovisual corrections, but not add dialogue, change story events or change the scene's factual order. Speech directions can specify when the existing utterance starts/stops and who stays silent; they are not new dialogue or a reason to invent visual content.

For SD2.5, preserve every non-stage line exactly, including reference definitions, total duration and style. Preserve each stage header, its entire 声音： clause, all dialogue in braces and all @图片/@音频 tags. Only clarify visible actions within the existing stage lines using retained source and image evidence. If the necessary fix requires changing a protected binding or source fact, stop and identify it; do not disguise a source correction as a prompt edit.

retake means the input is already correct and one new seed is justified. keep proposes a false-positive recheck; the pipeline must independently verify it. stop means there is no supported repair. Do not force an edit for every clip.

Only work on /work copies. Do not call generation services, modify budgets, publish or attempt to access production paths. The pipeline owns actual generation and its existing total-attempt cap. Produce one candidate per invocation. Only when the host explicitly returns a rejected candidate in primary.rejections may you revise it using the exact rejection reason, check evidence and input/rejected-candidate-N.txt file. The host permits at most three candidates including the first; each invocation retains its 600-second window and all original video limits. Correct the rejected request without changing protected source, dialogue, bindings or timing. If a compliant repair requires an allowed source correction, choose input; otherwise stop and give the precise unsupported change. Never retry autonomously or reinterpret service failures or exhausted budgets as permission for another candidate. Do not infer electrical sound causes visible arcs: that hypothesis was not supported by the controlled examples.

For romance_conversation_v1 SD requests, preserve all nonvisual lines exactly (including references, speakers, dialogue, performance and sound). Only the four existing 【开始状态】/【本段机位】/【动作与交流】/【结束状态】 lines may change; retain their headers and order.

## Repair the layer that is wrong

Check actual image evidence and distinguish postproduction captions from generated text. Cite the exact native frame before claiming text is baked into the native image. Identify the garment part actually visible: a jacket flap over a lap is not proof of red trousers. A visible far bank is not proof of a reachable path; verify same-side connection or feet on it. A script silent_action label is not proof of an actual silent audio segment. Check ASR windows, not just complete-dialogue CER.

For `input`, decision.json includes `source_quote` (verbatim source), optional `shots` (actual field:text pairs, e.g. [{"index":12,"camera":"same recording phone; phone stays outside its own frame"}]), optional `clip_ids` (include all clips sharing edited shots), and optional `assets` (e.g. [{"candidate_id":"reviewed-body"}] selecting reviewed alternatives in input/upstream.json). A reference-only selection omits `shots`; never write a literal `supported_source_fields` field. Select an existing reviewed candidate using `input`, not `asset`. Its approved source_quote may be cited for that asset selection. Only update camera, visual_prompt, motion_prompt, start_state, end_state, sfx, avoid or light; preserve dialogue, people, props, timing and source anchors. You may select a reviewed body_view candidate: the host retains the existing HD portrait for face identity and binds the complete clothing view to the same actor, not a second person. The host independently checks source, rebuilds the actual Chinese request with existing cuts, retranslates and checks the actual references. Without video permission, the checked proposal is staged and the active source/take are retained. Do not write prompt.txt for an input correction.

For `asset`, include `source_quote` plus `asset`={role:character|location,name:<existing reference name>,design:<character appearance object or location description>}. Prefer a reviewed existing candidate. Only request a new design when an actual visual state is missing. The host uses the existing image API, independent source/image review and immutable candidates; it does not overwrite old pictures. New or selected references are input changes, never take acceptance.

For `audio`, include `candidate_id` selecting a reviewed independent ambience audio in input/upstream.json. Use target=interaction for an existing voting card; for a novel without a card use target=clip only if this entire clip is authored silent_action and has no bound dialogue. A clip audio repair remuxes the assembly while copying video unchanged; never cut an existing spoken line to create silence. The host validates and adopts it into delivery policy, exports and reviews without generating a video. Do not choose English edits or a video retake for a tail-card assembly error. ASR silence does not prove no breathing; a synthesis source without voice input must be labelled as synthesis. Never claim subjective listening if it was not performed.

For `keep` disputing a final-movie finding, cite the actual supplied final JPEGs and explain the discrepancy. Optional `finding_ids` selects specific current issue IDs; omit it to ask about the target clip's current picture findings. The host may ask a separate independent Agent to adjudicate once for this clip and movie context, even when video and upstream allowances are exhausted; see final_dispute_remaining. Your keep does not clear findings. An uncertain image cannot prove a repair; describe uncertainty explicitly. A different defect (for example a watch instead of the alleged phone screen) is a separate finding and must remain actionable.

Repeated same defect after a corrected source/ref must stop for a changed strategy. Do not keep adding prohibitions or spend a new seed without a materially different supported input. Corrected inputs at zero video budget remain prepared with needs_video; the host alone may activate an explicitly authorized conditional take after validation. A stopped action never grants video permission.

Identity portraits constrain only visible design. Do not infer hidden clothes/equipment are absent or replace current-phase wardrobe with an old full-body description. Check actual visible contradictions and current source facts.

## Shared stability experience

In the recorded Guangming754 repair, the street reference was replaced by the
existing rooftop reference and an over-shoulder/frontal conflict was rewritten
as a same-camera, single-person three-quarter front shot. One new take was then
checked for native-frame motion, visible bodies and the remaining media gates.
This composite result does not establish that one camera phrase cures shaking.
In722 and752, fixed framing with localized subject movement yielded steadier
takes;722 also required a further repair after a steady take introduced a cloned
companion.752's formal targeted review remained incomplete, and722 retained an
unresolved background-prop finding. Recorded improvement is not universal or
whole-episode acceptance. Choose the camera from the current task and evidence;
historical examples do not override an approved moving shot or grant a retake.
