# Asset and shot design

## Contents

- Series bible
- Character assets
- Location assets
- Scene and shot grammar
- Dialogue construction
- Repetition control

## Series bible

Lock a style fingerprint, line treatment, palette, light behavior, costume vocabulary, typography, prohibited styles, and safety constraints before generating episode assets. Reuse the exact canonical identity descriptions in downstream prompts.

## Character assets

Create one package per recurring character before shot keyframes:

- canonical written specification;
- front, three-quarter, profile, and back views;
- full-body and chest-up proportions;
- neutral, happy, angry, afraid, sad, surprised, and determined expressions;
- canonical costume and any episode-specific costume variants;
- stable hair, face, age, accessories, colors, and distinguishing marks.

Keep the character isolated on a simple background in identity sheets. Approve the written specification before spending on final sheets when interactive approval is practical.

## Location assets

Create clean plates without foreground characters for every recurring location:

- establishing view and spatial map;
- primary conversation angle and reverse angle;
- detail inserts for story-critical props;
- fixed time, weather, light direction, architecture, and object anchors;
- allowed state changes such as damage, crowd density, or day-to-night transitions.

Use the same location identifier across consecutive shots. Change only the variables justified by the story.

## Scene and shot grammar

Give each scene one narrative job: establish, confront, reveal, reverse, decide, or resolve. Give each shot one dominant visual action.

Use this basic rhythm for dialogue:

```text
establishing or two-shot
speaker A close-up
listener B reaction
speaker B close-up
insert or environmental response
resolution two-shot or exit
```

Maintain the 180-degree line inside a conversation unless a deliberate emotional break justifies crossing it. Use establishing shots sparingly in vertical video; move quickly to readable faces and actions.

## Dialogue construction

- Use exactly one visible speaker per generated speaking clip.
- Frame a front or three-quarter close-up with the mouth clear enough for reference-audio animation.
- Keep the speaker in frame from before speech onset through at least 0.3 seconds after speech end.
- Keep other visible characters silent with closed mouths, or move their reactions into independent clips.
- Avoid camera cuts, face occlusion, profile extremes, large head turns, and fast motion during the utterance.
- Supply the exact turn audio as the reference audio and the exact text in the prompt.
- Use the same character asset, costume, location plate, palette, and light direction across reverse shots.

## Repetition control

Reuse assets, not finished compositions. Treat these as reusable: identity sheet, expression reference, costume, background plate, prop, palette, light, and lens language. Treat these as turn-specific: crop, speaker, expression beat, pose, foreground arrangement, and mouth-visible keyframe.

Reject a plan when different speakers reuse the same complete keyframe or when one visual composition remains on screen through multiple unrelated narration beats. Track recent shot scale, camera move, dominant color, and composition; avoid immediate repetition unless it is an intentional match cut.
