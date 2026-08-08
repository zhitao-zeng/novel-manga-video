---
name: novel-comic-drama-director
description: Convert Chinese novel files or chapter text into production-ready vertical manga-drama episodes with source-faithful splitting, reusable character and location assets, bounded LLM planning, reference-audio video generation, multi-speaker TTS, final subtitle burn-in, ASR, and deterministic media gates. Use for novel-to-video planning or implementation, character/location asset design, storyboards, model-neutral pipeline integration, manga-drama batch production, or repairing repeated shots, inconsistent characters, mismatched speech, and subtitle failures.
---

# Novel Comic Drama Director

Build a chapter-faithful 9:16 manga-drama pipeline. Reuse identities and locations, not complete dialogue compositions. Put the exact dialogue in the video prompt and pass the locked TTS audio as reference audio; do not add lip-sync inspection or postprocessing stages.

## Run the workflow

1. Read the nearest project instructions and inspect existing plans, assets, reports, and generated media before changing anything.
2. Preserve the input chapter boundary and source-to-shot trace. Treat one source chapter as one episode when chapters exist.
3. Lock the series bible, then build separate character and location asset libraries before episode keyframes.
4. Convert the episode into scenes, shots, and speech turns. Keep these levels distinct:
   - episode: one source chapter;
   - scene: one location/time/emotional phase;
   - shot: one visual beat and camera setup;
   - turn: one narrator or one visible speaker utterance.
5. Generate audio before speaking video. Bind each turn to one exact text string, one voice, one audio file, and one subtitle timing record.
6. Generate each visible dialogue turn from a speaker-specific close-up reference plus its reference audio. Reuse the location plate, palette, lighting, costume, and identity references; do not reuse one full multi-person keyframe for different speakers.
7. Render subtitles from the exact turn text and measured or forced-aligned speech boundaries.
8. Run the required media, subtitle, ASR, source-trace, and safety gates. Keep face consistency as a monitoring signal only.
9. Regenerate only units that fail required gates, then re-render and re-run final admission.

Read [production-contract.md](references/production-contract.md) before changing chapter splitting, output schemas, concurrency, or provider routing.

Read [asset-shot-design.md](references/asset-shot-design.md) when creating character assets, location plates, scenes, shots, keyframes, or video prompts.

Read [av-quality-gates.md](references/av-quality-gates.md) when producing audio, subtitles, dialogue clips, QC reports, or deciding whether an episode is deliverable.

## Enforce the non-negotiables

- Keep the story faithful to the source characters, relationships, event order, causality, and chapter ending.
- Enter chapter-specific action or conflict within ten seconds, including the fixed intro.
- Keep one dominant visual action and one speaking role per generated dialogue clip.
- Keep the visible speaker's face and mouth unobstructed for the full utterance so the reference-audio video model has a usable composition; place reactions in separate shots.
- Use the exact rendered audio as the subtitle timing source. Treat character-count interpolation as a fallback proxy, not word-level synchronization.
- Measure spoken-content accuracy with ASR and retain per-turn errors; an acceptable episode average must not hide badly wrong individual lines.
- Verify that subtitles were actually burned into the delivered pixels, not merely that an ASS file exists.
- Do not run screenshot-based mouth review, SyncNet-like scoring, LatentSync, or other lip-sync remediation. Occasional mouth mismatch is accepted by this pipeline policy.
- Prompt narrator shots as closed-mouth B-roll; do not add a separate mouth-motion admission gate.
- Preserve 1080x1920, 25 or 30 fps, H.264/AAC MP4 and 1080x1920 JPEG cover/end screens.

## Work within this repository

Prefer the existing project scripts and reports:

- `src/novel_manga/pipeline.py` and `production_runtime.py` are the canonical executable workflow; they must remain usable without Codex or this Skill directory;
- `src/novel_manga/production.py` compiles the scene/shot/turn hierarchy and creates reusable character/location assets;
- `src/novel_manga/runtime_backends.py` is the model-neutral command contract for alignment and ASR;
- `src/novel_manga/planner.py` implements bounded schema/domain validation feedback for OpenAI-compatible and command planners;
- `scripts/audit_multivoice_asr.py` for per-turn speech-content CER;
- `src/novel_manga/admission.py` for source trace, subtitle, ASR, and production admission;
- `src/novel_manga/qc.py` for container, resolution, frame-rate, audio, black-frame, silence, and freeze checks;
- `src/novel_manga/face_consistency.py` for non-blocking identity monitoring.

Do not call remote generation merely to test the Skill. Validate schemas and scripts locally first, then use the smallest authorized media probe.
