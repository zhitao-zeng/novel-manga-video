# Container / leaderboard SUT notes

The image is a Python/ffmpeg controller and does not require Codex. It starts one Uvicorn process on port 80 and
implements `/ready`, `/upload_novel`, `/generate_progress`, and `/download/{video|image}/...`.

Production uses one executable profile: command-planned short drama with video-model native dialogue. Inject the
planner, ASR, and hosted-media credentials at runtime; do not bake credentials or model files into the image.

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --cpus 4 --memory 32g -p 8080:80 \
  -v /path/to/output:/output \
  -e NOVEL_PROVIDER=phanrouter -e NOVEL_ADMISSION_MODE=production \
  -e NOVEL_FINAL_AUDIO_POLICY=native_dialogue \
  -e PHANROUTER_API_KEY \
  -e NOVEL_PLANNER_BACKEND=command -e NOVEL_PLANNER_COMMAND \
  -e NOVEL_ASR_COMMAND \
  novel-manga-video:0.13.0
```

The video provider must return an MP4 with native audio. ASR is mandatory in production because it supplies subtitles
and the native-dialogue hard checks. TTS, reference audio, forced alignment, local model supervision, and an offline
GPU image are not part of the current container.

Durable uploads, job state, and deliverables live under `/output`. Keep `NOVEL_JOB_WORKERS=1` on a 4-core host; video
generation concurrency is controlled independently by `NOVEL_VIDEO_WORKERS` (default 2).

The state store resumes an interrupted `processing` job after container restart. Multiple Uvicorn processes against
the same output directory are not supported. The diagnostic CLI remains available with:

```bash
docker run --rm --entrypoint novel-manga novel-manga-video:0.13.0 --help
```
