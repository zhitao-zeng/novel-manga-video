# Project instructions

- Never store API keys in source, configuration examples, logs, or generated manifests.
- Keep generated media and mounted models out of Git.
- Preserve the source-to-shot trace in every successful episode.
- A production episode is successful only after the media quality gate passes.
- Follow the selected production profile: 1080x1920 portrait or 1920x1080 landscape, 25 fps, H.264/AAC MP4 and JPEG cover/end screens.
- For planning, assets, H3 prompts, speech or repair changes, read `docs/production-lessons.md` and the relevant linked record. Keep source/input defects, model behavior, technical checks and content acceptance distinct.
