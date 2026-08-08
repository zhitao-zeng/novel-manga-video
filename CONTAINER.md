# Container run notes

The image is a self-contained Python/ffmpeg controller and does not require Codex. Model adapters and checkpoints are
mounted read-only under `/models`.

If the host output directory is not writable by UID 10001, use the current host user:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --gpus 'device=0' --cpus 4 --memory 32g \
  -v /path/to/input:/input:ro \
  -v /path/to/output:/output \
  -v /path/to/models:/models:ro \
  -e PHANROUTER_API_KEY \
  -e NOVEL_PLANNER_COMMAND -e NOVEL_TTS_COMMAND \
  -e NOVEL_ASR_COMMAND -e NOVEL_ALIGN_COMMAND \
  novel-manga-video:0.4.0 generate /input/novel.pdf \
  --novel-id 1 --title 小说名 \
  --provider phanrouter --admission-mode production --output /output
```

For an entirely local stack, set `NOVEL_IMAGE_COMMAND`, `NOVEL_VIDEO_COMMAND`, `NOVEL_TTS_COMMAND`, and run with
`--provider command`. Production also requires an ASR command. Lip-sync inspection and remediation are intentionally
disabled; exact dialogue and locked reference audio are sent directly to the video provider. Keep video concurrency at two on the submitted
4-core/32-GiB environment until the selected backend has been load-tested.
