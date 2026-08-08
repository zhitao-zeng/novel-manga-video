# Production contract

## Contents

- Input and episode boundaries
- Required hierarchy
- Output artifacts
- Provider and concurrency policy
- Resume and failure policy

## Input and episode boundaries

- Accept `txt`, Markdown, DOCX, PDF, or supplied text.
- When explicit chapters exist, preserve their titles, order, complete text, and one-chapter-to-one-episode mapping.
- When chapters do not exist, split only at sentence or paragraph boundaries and apply the configured 3,000-6,000-character policy.
- Retain a source quote and source hash for every visual beat. Reject cross-chapter leakage.

## Required hierarchy

Use stable identifiers and keep these objects separate:

```text
novel
  series_bible
  character_assets[]
  location_assets[]
  episodes[]
    scenes[]
      shots[]
        turns[]
```

Every turn must reference `episode_id`, `scene_id`, `shot_id`, `speaker_role`, exact `text`, `audio_path`, `subtitle_alignment`, `character_asset_ids`, `location_asset_id`, `keyframe_path`, and generated clip path.

Do not equate the number of scenes, keyframes, and speech turns. Multiple turns may share the same location and lighting, but a change of visible speaker normally requires a new speaker-specific composition.

## Output artifacts

Retain at minimum:

```text
series_assets/
  characters/<character_id>/spec.json
  characters/<character_id>/turnaround.jpeg
  characters/<character_id>/expressions.jpeg
  locations/<location_id>/spec.json
  locations/<location_id>/establishing.jpeg
episode/
  episode_plan.json
  content_trace.json
  work/turn_audio/
  work/keyframes/
  work/raw_video/
  work/subtitles.ass
  content_trace.json
  alignment_report.json
  asr_report.json
  face_consistency_report.json
  media_qc_report.json
  admission_report.json
  qc_report.json
  <video_id>.mp4
  <video_id>_cover.jpeg
  <video_id>_ending.jpeg
```

Store API task identifiers and request hashes for resumability, but never store credentials or authenticated URLs.

## Provider and concurrency policy

- Generate character/location/keyframe images with the configured image provider and reference inputs.
- Generate speaking video with the configured image-to-video provider, speaker keyframe, and exact reference audio.
- Default video submission concurrency to two. Increase only after observing provider stability, CPU/memory pressure, and rate limits.
- Keep Qwen TTS voice assignments stable for the full novel. Synthesize each turn independently so failures can be regenerated without changing other timings.
- Always synthesize and lock turn audio before video generation. Pass that file as reference audio and use the same locked audio in final composition; do not add a lip-sync postprocessor.

## Resume and failure policy

- Reuse an artifact only when its request identity hash still matches the current text, references, duration, provider, and model.
- Regenerate only failed or stale turns.
- Keep every chapter represented in the manifest even when generation fails.
- Mark an episode successful only after media QC, subtitles, source trace, and required ASR gates pass.
- Preserve prior versions; never overwrite the only known-good episode during an experiment.
