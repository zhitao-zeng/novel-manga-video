# Shot Contract production pipeline

This pipeline adapts the reusable contract and iteration ideas documented by
`renmu2017/Hell-Grind-AIGC-Skill`; it does not install that Skill at runtime or
copy its project tables. `production_plan.json` remains the repository's single
source of truth.

## Data flow

```text
source chapter
  -> EpisodePlan + ShowrunnerPlan
  -> scene / shot / turn
  -> SceneSpatialContract + location/character state versions
  -> EpisodeSequenceContract
  -> one RuntimeVisualGroup per shot and delivery identity
  -> model-independent ShotContract + ImagePromptContract
  -> optional ActionPhysicsPlan for physical/VFX shots
  -> provider-specific ProviderPromptAdapter
  -> generation attempt + iteration evidence
  -> selection / render / production admission
```

The API route never merges different visible speakers, narration, off-screen
dialogue, or inner voice into one generated performance. It also does not merge
different shots merely to remove a short cut. The local H3 route may explicitly
enable short-shot packing, but only while the delivery identity remains the same.

## Shot Contract

Every generated group records:

- one narrative goal and an exact duration;
- visible asset IDs and audible roles;
- reference `inherit` / `exclude` scopes;
- `open_state -> beat_timeline -> close_state`;
- `camera_start -> camera_path -> camera_end` with one primary move;
- `continuity_in -> changes_here -> continuity_out`;
- `must_hold / changes_here / must_not_appear`;
- at most three current risk codes;
- exact visible dialogue and an explicit audio-master policy (`external_audio_is_master`
  is true for locked reference audio and false for video-model native audio).

The sequence contract orders those groups, records the camera rhythm and common
lighting/audio continuity, visual motifs and coverage rhythm, and makes the prior
close state the next group's input. Dialogue coverage keeps the established screen
side while varying chest-up, tight shoulder, and wider waist-up compositions.

Physical shots use `preparation -> force -> contact -> reaction -> settling`, plus
only the environment feedback that the event can visibly cause. Ordinary dialogue
does not receive a decorative physics plan.

## Runtime references

The style master is an upstream asset-development reference only. It is not sent
with every story keyframe.

Ordinary single-speaker keyframes use exactly:

1. the current character asset, inheriting identity, hair, costume, and its 2D rendering;
2. the approved location asset version, inheriting architecture, space, color, daylight,
   and its 2D rendering.

Neither reference supplies the new pose, composition, or camera. Location proper
names stay in the internal contract; the hosted prompt receives a generic semantic
location such as `古代家族议事大厅室内` so names are not visualized as signs.

Story keyframes may bind multiple approved character assets plus one approved
location view when exact blocking, a critical prop interaction, or a relationship
move requires a composed start frame. The image contract records the exact subject
count and versioned asset IDs; each named character must appear once, while
off-screen-only mentions are excluded.

## Prompt adapter

The full director contract is retained for audit. GPT Image 2 and SD2.5 receive a
compact adapter containing only the subject/reference scopes, current location,
initial composition, one visible action, one camera move, exact audio-master rule,
close state, and the few highest-risk constraints. Project management fields,
unrelated safety boilerplate, and generic gesture templates are not sent.

For `sd25_native_original`, the adapter requests native audio and sends no reference
audio. The resulting clip must contain both video and AAC audio, and final assembly
keeps that native track. This policy is preview-only until delivered speech has been
audited by ASR.

## Failure and iteration records

Repairs record stable failure codes, responsibility layer, changed variables, a
falsifiable hypothesis, expected improvement, decision, and next action. Prompt,
asset, adapter, and random-sampling changes are separate iterations. A paid remote
task that succeeds but hits a transient CDN 404 or timeout is resumed instead of
being regenerated.

Known hard routing examples:

- `F-ACTION-OVERLOAD` or mixed delivery identities: split the shot/group;
- `F-REF-SCOPE` or `F-SPATIAL-RESET`: revise the asset/reference layer;
- `F-EXPOSURE` / `F-COLOR-DRIFT`: repair the keyframe before video submission;
- `F-TEXT`: remove proper-name visualization and require a text-free keyframe;
- repeated failure without improvement: stop sampling and return to its responsibility layer.

## Admission boundary

The keyframe preflight does not replace final production admission. Generated clips
first pass duration, native-audio/reference-audio, planned-action, exact-cast,
unexpected-object and screen-direction gates. Delivery still requires source trace,
1080x1920 H.264/AAC output, 25 or 30 fps, subtitle structure and pixel burn-in,
per-turn ASR, and media QC. Face consistency remains a monitoring signal rather than
a biometric or blocking gate.
