# Offline single-image runtime contract

## Delivery boundary

The submission is one OCI image with one entrypoint and one HTTP service. Model
weights are not copied into an image layer; the leaderboard mounts the pinned
weight tree read-only at `/models`. The image contains every executable,
library, adapter, font, and media utility required to run those weights with
outbound networking disabled.

One image does not mean one Python site-packages directory. The image owns the
following isolated virtual environments so dependency resolution is
reproducible and model stacks cannot silently upgrade each other:

| Runtime | Path | Responsibility |
| --- | --- | --- |
| controller | `/opt/venvs/controller` | HTTP API, chapter splitting, plans, orchestration, ffmpeg render and admission |
| planner | base-image `/usr/local` runtime | local Qwen3.5 OpenAI-compatible planner process |
| image | `/opt/venvs/image` | Z-Image-Turbo and Qwen-Image-Edit-2511 |
| h3 | `/opt/venvs/h3` | ComfyUI + MiniMax H3 Ref2VA |
| audio | `/opt/venvs/audio` | VoxCPM2, optional Qwen3-TTS fallback, Qwen3-ASR/ForcedAligner and SenseVoice evidence |

All runtimes are built into the same image. The user never activates an
environment or starts a sidecar.

## Mounted model contract

The container validates the following paths before reporting ready for
production. Model aliases may be symlinks inside the mounted tree, but their
resolved target must stay under `/models`.

```text
/models/qwen3.5-27b-awq/
/models/z-image-turbo/
/models/qwen-image-edit-2511/
/models/minimax-h3/
  diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
  text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors
  vae/minimax_h3_video_vae_fp16.safetensors
  vae/minimax_h3_audio_vae_fp32.safetensors
/models/voxcpm2/
/models/qwen3-asr/
/models/qwen3-forced-aligner-0.6b/
```

`/models/qwen3-tts-customvoice/` is optional when the default
`NOVEL_TTS_BACKEND=voxcpm2` is used. If mounted, it provides per-line automatic
fallback. It becomes required only when `NOVEL_TTS_BACKEND=qwen` is selected.

Readiness has two levels:

- `/ready` confirms the controller is alive and reports whether the required
  production model manifest is complete.
- A production upload fails closed before accepting work when any selected
  model or executable is absent. Preview mode may remain available for API
  diagnostics, but cannot claim submission eligibility.

## One-GPU stage order

Only one heavyweight model family owns the A100 at a time. A controller-owned
supervisor starts a worker from the appropriate runtime, waits for a model-load
probe, serves all work in that stage, then terminates the worker and verifies
that CUDA memory has been released before starting the next stage.

```text
parse/split (CPU)
  -> planner: story bible and every episode plan
  -> image/base: all no-reference character and scene assets
  -> image/edit: all reusable character expressions
  -> for each episode:
       audio: VoxCPM2 locked WAVs, ASR and forced alignment
       image/edit: continuous-shot keyframes and dedicated cover art
       MiniMax H3 Ref2VA: all shots receive a timing/performance WAV
       visible dialogue: locked TTS remains audible and drives the mouth
       narration/off-screen voice: duration-preserving silence keeps mouths closed
       audio-evidence: ASR of the actual delivered MP4 audio
       CPU render/QC: remux locked WAV, subtitles, endpoint cards and admission
```

The final render always remuxes the locked TTS WAV. H3-generated audio is
not allowed to replace or truncate the source dialogue. LatentSync and any lip
inspection/remediation stage remain disabled.

VoxCPM2 voice references are generated deterministically from fixed role
profiles and persisted under `/output/.runtime/voxcpm2-voices`. A user-provided
reference directory may be selected through `NOVEL_VOXCPM_REFERENCE_DIR`.
Delivered speech is normalized to -18 LUFS, resampled to 48 kHz and then passed
through the existing ASR/retry and forced-alignment gates.

## Batching and concurrency

- Z-Image and Qwen edit weights are never resident together. Calls are batched by
  operation, so a novel does not switch models per character or per shot.
- MiniMax H3 is one video stage. It defaults to one queued request on a single
  A100 because ComfyUI keeps one checkpoint resident; two-way concurrency must
  remain disabled until a controlled A100-80GB/4CPU/32GB probe proves it safe.
- H3 starts with ComfyUI `--gpu-only --disable-async-offload`: its roughly
  52 GB model/encoder/VAEs fit on the A100-80GB, while default CPU weight
  offload would exceed the leaderboard's 32 GB host-memory limit.
- Planner, image, video and audio workers are not concurrently resident. This
  is enforced by the supervisor rather than relying on prompt discipline.
- A failed request keeps its content-addressed inputs and partial output but
  does not keep a stale worker alive.

## Local image prompt policy

The command provider defaults to `NOVEL_LOCAL_IMAGE_PROMPT_POLICY=native-v5`.
The policy name is part of both asset and episode visual cache identities, so
changing compiler behavior cannot silently reuse an image made by an older
policy. `legacy` and `native-v1` through `native-v4` remain accepted for
reproducible comparisons and rollback; production should use `native-v5`.

The controller keeps one semantic asset/shot description, while the local
adapter compiles it differently for each mounted model:

- Z-Image-Turbo receives a short positive-only instruction. Character assets
  contain only the current character's identity and wardrobe; location assets
  contain only environment facts and use environment-specific rendering terms.
  Cast palettes, person anatomy terms, negative concepts, opaque style hashes,
  contact-sheet language, and cross-style bans are removed before inference.
  Native-v5 replaces the former PBR/game-cinematic anchor with concrete
  cinematic-realism controls for facial anatomy, matte materials, neutral
  color grading, directional light, and layered depth.
- Qwen-Image-Edit-2511 receives a direct edit command with a blank negative
  prompt. The compiler assigns explicit roles to the reference board, permits
  pose/camera changes, extracts the actual visual and camera constraints from
  continuous visual groups, and explicitly keeps environment surfaces free of
  readable symbols. Native-v5 treats old local references as identity and
  structure anchors rather than immutable render-style anchors, and requests a
  full repaint so plastic skin, oversized eyes, glossy costume materials, and
  poster-like framing are not inherited by default.
- The original semantic prompt, effective compiled prompt, policy, style
  family, task kind, reference mode, hashes, and output path are written to the
  image `.local.json` audit sidecar.

MiniMax H3 can additionally receive a character asset as Picture 1 and a
separate empty location asset as Picture 2. This is a tested low-latency image
path for single-character dialogue: H3 composites both reusable assets while
following the locked performance audio, without first generating a per-shot
Qwen keyframe. A character asset alone produces a studio-background character
portait and is therefore not a general replacement for scene-aware keyframes.
Multi-character blocking, prop interaction, covers, narration tableaux, and
shots whose first-frame composition is story-critical still use Qwen edit.
The submitted offline image keeps `NOVEL_LOCAL_VISUAL_STRATEGY=keyframe` as the
production default. A real A100 probe showed that direct character + empty
location conditioning can compose a usable performance, but may still spend
the opening frames on the empty environment before introducing the actor. Set
`NOVEL_LOCAL_VISUAL_STRATEGY=h3-direct-single-character` only for a controlled
experiment: a visual group with reference dialogue, one unambiguous on-screen
speaker and one available location asset uses the two-image H3 input. A
story-mentioned character explicitly kept off-screen remains in the provenance
cast but does not disable the experiment; ambiguous or true multi-character
blocking falls back to the scene-aware Qwen keyframe automatically. The
strategy participates in the visual cache identity and is recorded in each
video request sidecar.

## Build and run

The pinned ComfyUI checkout is a build-time third-party dependency and is not
stored in this repository. Prepare it at the revision recorded below before
building the offline image:

```bash
mkdir -p vendor
git clone https://github.com/Comfy-Org/ComfyUI.git vendor/ComfyUI
git -C vendor/ComfyUI checkout --detach 6f7cd7fceaaf60d2669b554936394a7412c6fde5
```

```bash
docker build -f Dockerfile.offline \
  -t novel-manga-video:0.12.0-offline-a100 .

docker run --rm --gpus 'device=0' --cpus 4 --memory 32g \
  -p 80:80 \
  -v /path/to/models:/models:ro \
  -v /path/to/output:/output \
  novel-manga-video:0.12.0-offline-a100
```

The container exposes `/ready`, `/upload_novel`, `/generate_progress`, and
`/download`. It accepts no cloud API key and runs with the Hugging Face,
Transformers, and Diffusers offline switches enabled.

This image does not contain a second offline screenplay or render pipeline.
It installs the same `novel_manga` Core used by the API image. The local CLI,
worker and supervisor are provider adapters only; changing back to hosted
models is an environment configuration change, not a source-tree switch.

## Pinned upstream source baseline

These revisions are the implementation baseline and must be recorded in the
image label and runtime manifest:

- ComfyUI: `6f7cd7fceaaf60d2669b554936394a7412c6fde5`
- Diffusers package: `0.38.0`
- VoxCPM package: `2.0.3`
- Qwen3-TTS package: `0.1.1`
- Qwen3-ASR package: `0.0.6`
- Qwen-Image examples/tools: `6b5e1f5cec987d404be5ac6657db3b9aacb56a89`

The ComfyUI inference source and its GPL-3.0 license are prepared locally under
the Git-ignored `vendor/ComfyUI` directory from the revision above. It is a
build-context dependency and does not contain model weights.
One documented headless-only patch changes missing optional workflow-template
gallery assets from an error log to a warning; it does not touch nodes, model
loading, conditioning, sampling, decoding, or output. The image records this as
`io.novel-manga.comfyui-patch=headless-template-gallery-warning`.
The small source-audio lock node and its AGPL-3.0 notice are shipped under
`runtime/comfyui_custom_nodes/novel_manga_h3_audio_drive`. Model revisions
remain separate and are checked through the mounted model manifest.

Primary upstreams:

- <https://github.com/Comfy-Org/ComfyUI>
- <https://github.com/huggingface/diffusers>
- <https://github.com/QwenLM/Qwen-Image>
- <https://github.com/OpenBMB/VoxCPM>
- <https://github.com/QwenLM/Qwen3-TTS>
- <https://github.com/QwenLM/Qwen3-ASR>

## Acceptance sequence

1. Build the image without model weights or credentials.
2. Run controller unit and command-contract tests in the built image.
3. Mount the pinned model tree and run one real artifact per model family.
4. Record wall time, peak CUDA allocation, `nvidia-smi` peak, and process RSS.
5. Run `雨后小故事` with container networking disabled.
6. Accept only if the starting-kit HTTP contract and final 1080x1920,
   25fps H.264/AAC/JPEG, subtitle, speech-content and black/freeze gates pass.
