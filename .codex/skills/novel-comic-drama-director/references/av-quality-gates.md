# Audiovisual quality gates

## Contents

- Evidence states
- Subtitle gates
- Speech-content gates
- Visual consistency monitoring
- Remediation routing
- Admission rule

## Evidence states

Use three states:

- `passed`: the required measurement or review exists and meets its threshold;
- `failed`: evidence exists and violates the threshold;
- `inconclusive`: required evidence is absent, incompatible, or too weak to decide.

Never convert `inconclusive` into `passed`. A production episode cannot pass while any required turn remains failed or inconclusive.

## Subtitle gates

Check all of the following:

1. Reconstruct exact turn text from ASS pages and compare it with the locked script.
2. Preserve ordering and role identifiers.
3. Allow at most two lines and prevent punctuation-only pages.
4. Prevent overlaps and negative/zero durations.
5. Keep the style in the 1080x1920 safe area with bottom alignment, adequate margins, readable font size, outline, and contrast.
6. Keep Chinese reading speed at or below the configured maximum; report every over-limit page.
7. Bind subtitle onset/end to measured speech or forced alignment. Label character-count interpolation as `coarse_audio_bounds`, not word alignment.
8. Compare the subtitle-free joined video with the delivered video at sampled subtitle times to confirm visible burned-in pixels in the subtitle region.

OCR is optional additional evidence. It does not replace exact source reconstruction because stylized Chinese OCR can be wrong.

## Speech-content gates

- Run ASR on the actual delivered audio for every turn.
- Report normalized reference, hypothesis, character error count, and CER per turn.
- Enforce both an episode aggregate threshold and a per-turn threshold.
- Do not let a low aggregate CER hide a missing or badly misread short line.
- Use a protected pronunciation dictionary or regenerate audio for names, places, ranks, and other proper nouns.
- Keep ASR evidence scoped to spoken-content accuracy; it does not claim exact mouth timing.

## Visual consistency monitoring

Record lightweight face/identity consistency against the locked character asset. Use it to find likely character drift, but do not treat it as biometric identification and do not block admission or automatically regenerate media from this score.

For visible dialogue generation, use these prompt-time constraints only:

- one visible speaking role per clip;
- speaker-specific close-up asset;
- exact dialogue embedded in the video prompt;
- exact locked TTS file supplied as reference audio;
- no LatentSync, screenshot mouth inspection, SyncNet-like gate, or lip-sync remediation pass.

## Remediation routing

- Wrong or unclear words: regenerate Qwen TTS, adjust pronunciation instructions, then rerun ASR.
- Subtitle timing drift: generate forced alignment or synthesize shorter subtitle pages independently; do not hand-shift the whole episode.
- Speaker too small, obscured, or wrong: regenerate a speaker-specific keyframe and video turn.
- Burn-in absent: rerender from the retained ASS and verify delivered pixels.

## Admission rule

Pass an episode only when:

```text
media_qc == passed
subtitle_structure == passed
subtitle_burn_in == passed
speech_content == passed
source_trace == passed
```

Record threshold values, backend names, model/checkpoint revisions, per-turn ASR evidence, and non-blocking face-consistency metrics in the final report.
