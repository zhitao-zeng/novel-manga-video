"""Does a character keep the same voice across episodes? (speaker-embedding version)

Every ASR chunk the runner matched to script lines belonging to exactly one
speaker is turned into a CAM++ speaker embedding.  Then three numbers per
character:

  within  - mean cosine similarity between that character's chunks inside one
            episode (the ceiling: the same generated voice, different lines)
  across  - mean cosine similarity between chunks from different episodes
  others  - mean similarity to other characters' chunks (the floor)

If across is close to within, the voice carries between episodes.  If across
sits near others, the character is re-cast every episode.

    speaker_probe.py outputs/zhutian-fast outputs/zhutian-card
"""
from __future__ import annotations

import itertools
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

MODEL = "/mnt/disk1/zengzhitao/models/speaker/campplus_sv_zh-cn.onnx"
MIN_SECONDS = 0.6          # CAM++ needs a little audio to be meaningful
SAME_SPEAKER = 0.55        # the usual operating point for this model


def extractor() -> sherpa_onnx.SpeakerEmbeddingExtractor:
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=4, debug=False, provider="cpu")
    if not config.validate():
        raise SystemExit(f"speaker model not usable: {MODEL}")
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def speaker_of(chunk: dict, lines: list[dict]) -> str | None:
    matched = str(chunk.get("matched_lines") or "")
    if not matched:
        return None
    speakers = {line["speaker_name"] for line in lines
                if line.get("text") and len(line["text"]) >= 3 and line["text"][:4] in matched}
    return speakers.pop() if len(speakers) == 1 else None


def embeddings(novel_dirs: list[Path]) -> dict[str, dict[str, list[np.ndarray]]]:
    """character -> episode -> embeddings."""
    model = extractor()
    out: dict[str, dict[str, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
    for novel_dir in novel_dirs:
        for directory in sorted(d for d in novel_dir.iterdir() if d.is_dir() and d.name.startswith(novel_dir.name + "_")):
            report_path, plan_path = directory / "thin_media_report.json", directory / "clip_plan.json"
            if not (report_path.is_file() and plan_path.is_file()):
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            plan = {clip["clip_id"]: clip for clip in json.loads(plan_path.read_text(encoding="utf-8"))["clips"]}
            for clip in report["clips"]:
                selected = clip.get("selected") or {}
                video, lines = selected.get("video"), (plan.get(clip["clip_id"]) or {}).get("lines") or []
                if not video or not lines:
                    continue
                wav = Path(video).parent / "native.wav"
                if not wav.is_file():
                    continue
                audio, rate = sf.read(wav, dtype="float32", always_2d=False)
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                for chunk in selected.get("chunks", []):
                    if chunk.get("subtitle") != "script_span":
                        continue
                    who = speaker_of(chunk, lines)
                    start, end = float(chunk["start"]), float(chunk["end"])
                    if not who or end - start < MIN_SECONDS:
                        continue
                    span = audio[int(start * rate):int(end * rate)]
                    if len(span) < int(MIN_SECONDS * rate):
                        continue
                    stream = model.create_stream()
                    stream.accept_waveform(sample_rate=rate, waveform=span)
                    stream.input_finished()
                    if not model.is_ready(stream):
                        continue
                    vector = np.asarray(model.compute(stream), dtype=np.float32)
                    norm = float(np.linalg.norm(vector))
                    if norm > 0:
                        out[who][directory.name].append(vector / norm)
    return out


def mean_similarity(left: list[np.ndarray], right: list[np.ndarray] | None = None) -> float | None:
    if right is None:
        pairs = list(itertools.combinations(left, 2))
    else:
        pairs = [(a, b) for a in left for b in right]
    if not pairs:
        return None
    return float(statistics.mean(float(np.dot(a, b)) for a, b in pairs))


def main() -> int:
    novels = [Path(argument).resolve() for argument in sys.argv[1:]] or [Path("outputs/zhutian-fast").resolve()]
    data = embeddings(novels)
    everything = {who: [vector for vectors in episodes.values() for vector in vectors] for who, episodes in data.items()}

    print("角色 | 集数 | 片段 | 同集内相似度 | 跨集相似度 | 与其他角色 | 判断")
    print("---|---|---|---|---|---|---")
    rows = []
    for who, episodes in sorted(data.items(), key=lambda item: -sum(len(v) for v in item[1].values())):
        usable = {episode: vectors for episode, vectors in episodes.items() if vectors}
        if len(usable) < 2 or sum(len(v) for v in usable.values()) < 4:
            continue
        within = [value for vectors in usable.values() if (value := mean_similarity(vectors)) is not None]
        across = [mean_similarity(a, b) for a, b in itertools.combinations(usable.values(), 2)]
        across = [value for value in across if value is not None]
        others = [mean_similarity(everything[who], vectors) for other, vectors in everything.items() if other != who]
        others = [value for value in others if value is not None]
        if not across:
            continue
        w = statistics.mean(within) if within else None
        a = statistics.mean(across)
        o = statistics.mean(others) if others else float("nan")
        verdict = "跨集是同一个人" if a >= SAME_SPEAKER else ("跨集像换了人" if a <= o + 0.08 else "跨集不稳定")
        rows.append((who, len(usable), sum(len(v) for v in usable.values()), w, a, o, verdict))
        shown = f"{w:.2f}" if w is not None else "-"
        print(f"{who} | {len(usable)} | {sum(len(v) for v in usable.values())} | {shown} | {a:.2f} | {o:.2f} | {verdict}")

    if rows:
        withins = [r[3] for r in rows if r[3] is not None]
        print(f"\n参考：这个模型上 0.55 以上一般判为同一人；片段短、带现场杂音，绝对值偏低，看三档的相对关系。"
              f"\n跨集 {statistics.mean(r[4] for r in rows):.2f}，同集内 {statistics.mean(withins):.2f}，与其他角色 {statistics.mean(r[5] for r in rows):.2f}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
