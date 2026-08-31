# Container / leaderboard SUT notes

The image is a self-contained Python/ffmpeg controller and does not require Codex. It starts Uvicorn on port 80 and
implements `/ready`, `/upload_novel`, `/generate_progress`, and `/download/{video|image}/...`. Model adapters and
checkpoints are mounted read-only under `/models`; durable uploads, job state, and deliverables live under `/output`.

If the host output directory is not writable by UID 10001, use the current host user:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  --gpus 'device=0' --cpus 4 --memory 32g -p 8080:80 \
  -v /path/to/output:/output \
  -v /path/to/models:/models:ro \
  -e NOVEL_PROVIDER=phanrouter -e NOVEL_ADMISSION_MODE=production \
  -e PHANROUTER_API_KEY \
  -e NOVEL_PLANNER_COMMAND -e NOVEL_TTS_COMMAND \
  -e NOVEL_ASR_COMMAND -e NOVEL_ALIGN_COMMAND \
  novel-manga-video:0.13.0
```

For an entirely local stack, build `Dockerfile.offline`. It runs the same Core
with command adapters and a single-GPU model supervisor; it is not a forked
pipeline. Production also requires ASR evidence. Lip-sync inspection and
remediation are intentionally disabled; exact dialogue and locked reference
audio are sent directly to the video provider. Keep `NOVEL_JOB_WORKERS=1` on
the submitted 4-core/32-GiB environment.

The HTTP process is intentionally one Uvicorn worker. In-process locks prevent duplicate submissions for a novel ID;
the durable state store resumes an interrupted `processing` job after a container restart. Running multiple Uvicorn
processes against the same `/output` directory is not supported.

The old CLI remains available for diagnostics by overriding the image entrypoint:

```bash
docker run --rm --entrypoint novel-manga novel-manga-video:0.13.0 --help
```
