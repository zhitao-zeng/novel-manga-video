# Part B：人脸 gate（搁置）

用户于2026-10-09明确搁置。本文件保留原独立章节及校准止损判断，不执行安装、模型加载、校准、接入或配置变更。
这不是已验证方案。重新启动之前必须更新真实接口，修正SCRFD关键点坐标中的stride计算；
校准应使用人工确认的“身份正确/错误”标签，不能把任何原因的审片失败都视为脸错。
全局max只证明存在一张匹配的脸，不证明所有应入镜角色都正确；校准不可分就停止，不接硬门。
原实现/测试草稿留在下文备查，包含旧clip字段和旧模块路径，不能直接执行。

当前实施范围见[台账与诊断计划](2026-10-09-ledger-facegate-state.md)。

---

# Part B:人脸相似度硬 gate

### Task B1: 人脸检测+embedding 模块

**Files:**
- Create: `src/novel_manga/application/review/face_match.py`
- Modify: `pyproject.toml`(加依赖)
- Test: `tests/test_face_match.py`

**Interfaces:**
- Produces:
  - `FaceGate(model_dir: Path, min_det_score: float = 0.5)`:惰性加载 `scrfd_10g_bnkps.onnx`(检测)与 `glintr100.onnx`(ArcFace R100)。
  - `FaceGate.faces(image: numpy.ndarray) -> list[Face]`;`Face = dataclass(bbox: tuple, kps: numpy.ndarray(5,2), det_score: float)`
  - `FaceGate.embed(image, face) -> numpy.ndarray(512,)`(L2 归一化)
  - `best_similarity(clip_frames: list[Path], card_images: list[Path], gate: FaceGate) -> float`:所有(帧脸 × 卡脸)余弦相似度的最大值;任一侧无脸返回 `None`

- [ ] **Step 0: 装依赖(先查库存再装)**

```bash
grep -i -E 'onnx|opencv' /mnt/disk1/zengzhitao/docs/python-environment-inventory.md
.venv/bin/pip install onnxruntime opencv-python-headless numpy   # 优先 UV 缓存/离线轮子;装不上就停,报给用户
```
在 `pyproject.toml` dependencies 追加 `onnxruntime`、`opencv-python-headless`、`numpy`。确认模型文件存在:`ls /mnt/disk1/zengzhitao/models/faceswap/antelopev2/`(应有 `scrfd_10g_bnkps.onnx`、`glintr100.onnx`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_face_match.py
import numpy as np
from pathlib import Path
from novel_manga.application.review.face_match import FaceGate, best_similarity

MODEL_DIR = Path("/mnt/disk1/zengzhitao/models/faceswap/antelopev2")

def test_no_faces_returns_empty(tmp_path):
    gate = FaceGate(MODEL_DIR)
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    assert gate.faces(blank) == []

def test_best_similarity_none_when_no_faces(tmp_path):
    gate = FaceGate(MODEL_DIR)
    assert best_similarity([], [], gate) is None

def test_similarity_identity_is_one(tmp_path):
    """同一向量自比对必须为 1(数值管线冒烟,不依赖真脸)。"""
    gate = FaceGate(MODEL_DIR)
    v = np.random.RandomState(0).rand(512).astype(np.float32)
    v /= np.linalg.norm(v)
    assert abs(float(v @ v) - 1.0) < 1e-5
```

真脸冒烟(不进 CI,手动跑):用 `outputs/guangming-ds-100/series_assets/characters/character_001/turnaround.jpeg` 自比,期望 best_similarity ≥ 0.6;与另一角色的卡比,期望 < 0.4。结果记进 Task B2 校准报告。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_match.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 `face_match.py`**

```python
"""确定性人脸相似度检查:scrfd 检测 + glintr100(ArcFace R100)embedding。
MLLM 看脸不可靠(StoryAgent 实证),这层只做"帧里没人长得像该出场的人"的兜底。"""
from __future__ import annotations
import cv2
import numpy as np
from dataclasses import dataclass
from pathlib import Path

ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

@dataclass
class Face:
    bbox: tuple[float, float, float, float]
    kps: np.ndarray
    det_score: float

class FaceGate:
    def __init__(self, model_dir: Path, min_det_score: float = 0.5):
        import onnxruntime as ort
        self.min_det = min_det_score
        opts = ort.SessionOptions()
        self.det = ort.InferenceSession(str(model_dir / "scrfd_10g_bnkps.onnx"), opts, providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(str(model_dir / "glintr100.onnx"), opts, providers=["CPUExecutionProvider"])
        self._stride_anchors = {s: self._anchors(s) for s in (8, 16, 32)}

    @staticmethod
    def _anchors(stride, size=80):  # 640/stride=80 仅 stride=8;通用写法:
        # 实际按输入 640×640:size = 640 // stride
        size = 640 // stride
        ys, xs = np.mgrid[:size, :size]
        pts = np.stack([xs, ys], axis=-1).reshape(-1, 1, 2) * stride
        return np.tile(pts, (1, 2, 1)).reshape(-1, 2).astype(np.float32)  # num_anchors=2

    def faces(self, image: np.ndarray) -> list[Face]:
        img, scale = self._letterbox(image, 640)
        blob = cv2.dnn.blobFromImage(img, 1.0 / 128, (640, 640), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.det.run(None, {self.det.get_inputs()[0].name: blob})
        # 输出顺序:score_8,score_16,score_32,bbox_8,bbox_16,bbox_32,kps_8,kps_16,kps_32
        scores, bboxes, kps = outs[0:3], outs[3:6], outs[6:9]
        faces: list[Face] = []
        for i, stride in enumerate((8, 16, 32)):
            s = scores[i].reshape(-1)
            keep = np.where(s >= self.min_det)[0]
            if not len(keep):
                continue
            anc = self._stride_anchors[stride][keep]
            dist = bboxes[i].reshape(-1, 4)[keep] * stride
            x1 = (anc[:, 0] - dist[:, 0]) / scale; y1 = (anc[:, 1] - dist[:, 1]) / scale
            x2 = (anc[:, 0] + dist[:, 2]) / scale; y2 = (anc[:, 1] + dist[:, 3]) / scale
            k = kps[i].reshape(-1, 5, 2)[keep]
            k = (k + anc[:, None, :]) / scale  # kps 是相对 anchor 的偏移×stride
            for j in range(len(keep)):
                faces.append(Face((x1[j], y1[j], x2[j], y2[j]), k[j] * stride / 1.0, float(s[keep[j]])))
        return self._nms(faces)

    def embed(self, image: np.ndarray, face: Face) -> np.ndarray:
        M, _ = cv2.estimateAffinePartial2D(face.kps, ARCFACE_DST, method=cv2.LMEDS)
        aligned = cv2.warpAffine(image, M, (112, 112), borderValue=0.0)
        blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        v = self.rec.run(None, {self.rec.get_inputs()[0].name: blob})[0][0]
        return v / np.linalg.norm(v)

    @staticmethod
    def _letterbox(img, size):
        h, w = img.shape[:2]
        scale = size / max(h, w)
        resized = cv2.resize(img, (int(w * scale), int(h * scale)))
        canvas = np.zeros((size, size, 3), dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        return canvas, scale

    @staticmethod
    def _nms(faces, iou=0.4):
        if not faces:
            return faces
        boxes = np.array([f.bbox for f in faces]); scores = np.array([f.det_score for f in faces])
        order = scores.argsort()[::-1]; keep = []
        while len(order):
            i = order[0]; keep.append(i)
            if len(order) == 1: break
            xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0]); yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2]); yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            area_j = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
            order = order[1:][inter / (area_i + area_j - inter + 1e-9) <= iou]
        return [faces[i] for i in keep]

def best_similarity(clip_frames: list[Path], card_images: list[Path], gate: FaceGate) -> float | None:
    frame_faces, card_embs = [], []
    for p in clip_frames:
        img = cv2.imread(str(p))
        if img is not None:
            frame_faces += [(img, f) for f in gate.faces(img)]
    for p in card_images:
        img = cv2.imread(str(p))
        if img is not None:
            card_embs += [gate.embed(img, f) for f in gate.faces(img)]
    if not frame_faces or not card_embs:
        return None
    best = -1.0
    for img, f in frame_faces:
        e = gate.embed(img, f)
        best = max(best, max(float(e @ c) for c in card_embs))
    return best
```

注意:实现时先用 Task B1 Step 1 的真脸冒烟验证 decode 正确性;若 scrfd 输出张量顺序/anchor 数与假设不符(用 `sess.get_outputs()` 打印 shape 核对),按实际 shape 调整,**decode 正确性以"同卡自比 ≥0.6、异卡 <0.4"为准**,不以代码直觉为准。

- [ ] **Step 4: 测试 + 全量回归**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_match.py -v`
Expected: PASS
Run: `PYTHONPATH=src .venv/bin/python -m pytest -x -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add src/novel_manga/application/review/face_match.py pyproject.toml tests/test_face_match.py
git commit -m "review: 人脸相似度模块(scrfd+glintr100,本地 CPU ONNX)"
```

### Task B2: 阈值校准(dry-run 评估器 + 实跑)

**Files:**
- Create: `scripts/face_gate_eval.py`
- Test: `tests/test_face_gate_eval.py`(只测聚合逻辑,不跑模型)

**Interfaces:**
- Consumes: Task B1 的 `FaceGate`/`best_similarity`;`clip_plan.json` 的 `clips[].references`(role=="character" 的项含 `path`,相对 novel_dir);`application/review/evidence.py:100 clip_frames(video, output_dir, count)`。
- Produces: `python scripts/face_gate_eval.py --novel-dir <dir> [--episodes N] [--sample M]` → 打印每 clip 的 best_similarity 分布(已验收/未过分组),输出建议阈值;**只读,不写任何在产文件**(帧缓存写 `/tmp/face-gate-eval-*`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_face_gate_eval.py
from scripts.face_gate_eval import summarize, suggest_threshold

def test_summarize_groups_by_accepted():
    rows = [{"episode": "b_1", "clip": "clip_01", "score": 0.61, "accepted": True},
            {"episode": "b_1", "clip": "clip_02", "score": 0.22, "accepted": False}]
    s = summarize(rows)
    assert s["accepted"]["count"] == 1 and abs(s["accepted"]["min"] - 0.61) < 1e-9
    assert s["rejected"]["min"] == 0.22

def test_suggest_threshold_below_accepted_tail():
    rows = [{"score": 0.5 + i * 0.01, "accepted": True} for i in range(50)]
    rows += [{"score": 0.2, "accepted": False}, {"score": 0.25, "accepted": False}]
    t = suggest_threshold(rows)
    assert 0.25 < t < 0.5
```

- [ ] **Step 2: 确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_gate_eval.py -v`
Expected: FAIL,`ModuleNotFoundError`

- [ ] **Step 3: 实现 `scripts/face_gate_eval.py`**

薄 CLI 壳,核心两个纯函数(上面测试钉住)+ 一个采样主流程:

```python
"""人脸 gate 阈值校准:对已验收集回放相似度,只看不动。
用法: PYTHONPATH=src .venv/bin/python scripts/face_gate_eval.py --novel-dir outputs/X [--episodes 20] [--sample 60]"""
import argparse, json, random, statistics, sys, tempfile
from pathlib import Path

def summarize(rows: list[dict]) -> dict:
    out = {}
    for key, pred in (("accepted", lambda r: r["accepted"]), ("rejected", lambda r: not r["accepted"])):
        scores = [r["score"] for r in rows if pred(r) and r["score"] is not None]
        out[key] = {"count": len(scores),
                    "min": min(scores) if scores else None,
                    "p05": (statistics.quantiles(scores, n=20)[0] if len(scores) >= 20 else None),
                    "median": statistics.median(scores) if scores else None}
    return out

def suggest_threshold(rows: list[dict]) -> float:
    """取已验收分布的 5 分位再下压 0.05;样本不足 20 条时用 min-0.05。"""
    acc = sorted(r["score"] for r in rows if r["accepted"] and r["score"] is not None)
    if not acc:
        raise SystemExit("没有已验收样本,无法校准")
    tail = statistics.quantiles(acc, n=20)[0] if len(acc) >= 20 else acc[0]
    return round(max(0.15, tail - 0.05), 3)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel-dir", required=True, type=Path)
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--sample", type=int, default=60)
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from novel_manga.application.review.face_match import FaceGate, best_similarity
    from novel_manga.application.review.evidence import clip_frames
    gate = FaceGate(Path("/mnt/disk1/zengzhitao/models/faceswap/antelopev2"))
    novel = args.novel_dir
    episodes = sorted(novel.glob(f"{novel.name}_*/episode_review.json"))[: args.episodes]
    rows = []
    rng = random.Random(0)
    for review_path in episodes:
        ep_dir = review_path.parent
        plan = json.loads((ep_dir / "clip_plan.json").read_text())
        review = json.loads(review_path.read_text())
        for clip in plan["clips"]:
            if clip.get("kind") != "video":
                continue
            refs = [novel / r["path"] for r in clip.get("references", []) if r.get("role") == "character"]
            if not refs:
                continue
            verdict = review.get("clips", {}).get(clip["id"], {})
            video = verdict.get("video")
            if not video or not Path(video).exists():
                continue
            with tempfile.TemporaryDirectory(prefix="face-gate-eval-") as td:
                frames = clip_frames(Path(video), Path(td), 4)
                score = best_similarity(frames, refs, gate)
            rows.append({"episode": ep_dir.name, "clip": clip["id"], "score": score,
                         "accepted": verdict.get("severity") == "pass"})
            if len(rows) >= args.sample:
                break
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    print("suggested_threshold:", suggest_threshold(rows))

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 测试 + 实跑校准**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_gate_eval.py -v`
Expected: PASS
实跑:

```bash
cd /mnt/disk1/zengzhitao/novel-manga-video
PYTHONPATH=src .venv/bin/python /mnt/disk1/zengzhitao/novel/worktrees/episode-agent/scripts/face_gate_eval.py --novel-dir outputs/guangming-ds-100 --episodes 20 --sample 60
```
Expected: 已验收组的 p05 明显高于未过组;把分布和建议阈值写进 `docs/production-lessons.md`。若两组分布**不可分**(重叠严重),停——说明"卡 vs 成片帧"在 ArcFace 空间不可靠,如实报告用户,Part B 止于此(不要硬上线)。

- [ ] **Step 5: Commit**

```bash
git add scripts/face_gate_eval.py tests/test_face_gate_eval.py docs/production-lessons.md
git commit -m "review: 人脸 gate 阈值校准器 + 光明纪元回放分布记录"
```

### Task B3: 接入审片主流程 + retier 豁免

**Files:**
- Modify: `src/novel_manga/application/review/episode.py`(主循环 :67-135,在 `judge_clip` 调用前插入 gate)
- Modify: `src/novel_manga/application/review/retier.py`(跳过带 `hard_gate` 标记的行)
- Modify: `configs/pipeline.json`(加 `face_gate` 默认段)+ `tests/fixtures/runtime_config_before.json`(同步刷新)
- Test: `tests/test_face_gate_integration.py`

**Interfaces:**
- Consumes: B1 `FaceGate`/`best_similarity`;B2 校准出的阈值(写进 `configs/pipeline.json` 的 `face_gate.threshold`);`review/storage.py:7 take_identity`;`review/policy.py:59 flag_line` 的 flags 行格式。
- Produces: `episode_review.json` 的 clip 记录新增可选字段 `hard_gate: {"kind": "face_match", "score": float, "threshold": float}`;命中时该行 `severity:"fail"`、`tier:"must_fix"`、`identity_ok:false`、`identity_issue:"人脸与角色卡相似度 {score:.2f} 低于阈值 {threshold:.2f}"`、`feedback[cid]` 同文本、`video`/`take` 取当前文件。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_face_gate_integration.py
import json
from pathlib import Path
from novel_manga.application.review import episode as review_episode_mod
from novel_manga.application.review import retier
from novel_manga.application.review.storage import take_identity
from novel_manga.application.repair.managed import candidates

def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))

def _fixture_episode(tmp_path, monkeypatch):
    """最小一集:1 个 video clip、引用 1 张角色卡、假视频文件。"""
    novel = tmp_path / "book"; ep = novel / "book_001"
    video = ep / "work" / "clips" / "clip_01" / "attempt_01" / "clip.mp4"
    video.parent.mkdir(parents=True); video.write_bytes(b"fake")
    card = novel / "series_assets" / "characters" / "character_001" / "turnaround.jpeg"
    card.parent.mkdir(parents=True); card.write_bytes(b"fake")
    _write(ep / "clip_plan.json", {"clips": [{
        "id": "clip_01", "kind": "video", "cast": ["甲"],
        "references": [{"tag": "@图片1", "role": "character", "name": "甲",
                        "asset_id": "character_001",
                        "path": "series_assets/characters/character_001/turnaround.jpeg"}]}]})
    _write(ep / "thin_media_report.json",
           {"clips": [{"id": "clip_01", "selected": {"video": str(video)}}]})
    return novel, ep, video

def test_gate_fires_and_repair_accepts(tmp_path, monkeypatch):
    novel, ep, video = _fixture_episode(tmp_path, monkeypatch)
    monkeypatch.setattr(review_episode_mod, "_face_best_similarity",
                        lambda **kw: 0.10)  # 远低于任何阈值
    monkeypatch.setenv("NOVEL_FACE_GATE", "1")
    monkeypatch.setenv("NOVEL_FACE_GATE_THRESHOLD", "0.4")
    review_episode_mod.review_episode(ep)
    review = json.loads((ep / "episode_review.json").read_text())
    row = review["clips"]["clip_01"]
    assert row["severity"] == "fail" and row["tier"] == "must_fix"
    assert row["hard_gate"]["kind"] == "face_match" and row["identity_ok"] is False
    assert row["video"] == str(video) and row["take"] == list(take_identity(video))
    assert review["feedback"]["clip_01"]
    # 修复路由必须真的接受这条记录(否则 gate 白写)
    wanted, blocked = candidates(ep, review=review)
    assert "clip_01" in [c if isinstance(c, str) else c.get("id") for c in wanted] or "clip_01" in str(wanted)

def test_gate_skips_clip_without_character_refs(tmp_path, monkeypatch):
    novel, ep, video = _fixture_episode(tmp_path, monkeypatch)
    plan = json.loads((ep / "clip_plan.json").read_text())
    plan["clips"][0]["references"] = [{"tag": "@图片1", "role": "location",
                                       "path": "series_assets/locations/loc_001/establishing.jpeg"}]
    _write(ep / "clip_plan.json", plan)
    called = []
    monkeypatch.setattr(review_episode_mod, "_face_best_similarity",
                        lambda **kw: called.append(1) or 0.0)
    monkeypatch.setenv("NOVEL_FACE_GATE", "1")
    review_episode_mod.review_episode(ep)
    assert called == [], "无角色卡引用的 clip 不应触发人脸比对"

def test_gate_off_by_default(tmp_path, monkeypatch):
    novel, ep, video = _fixture_episode(tmp_path, monkeypatch)
    monkeypatch.delenv("NOVEL_FACE_GATE", raising=False)
    monkeypatch.setattr(review_episode_mod, "_face_best_similarity",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("不应被调用")))
    review_episode_mod.review_episode(ep)  # 不炸即通过(走原有 judge 路径,judge 也 mock 或容忍)

def test_retier_preserves_hard_gate_rows(tmp_path, monkeypatch):
    """retier 只对 severity=='fail' 的行重算 tier(retier.py:70-73),硬 gate 行必须豁免。
    入口形态照抄 tests/test_script_check.py:82-106:monkeypatch argv + 调 main()。"""
    import sys
    import novel_manga.application.review.retier as retier_reviews
    novel = tmp_path / "book"
    ep = novel / "book_001"
    ep.mkdir(parents=True)
    (novel / "story_bible.json").write_text("{}", encoding="utf-8")
    review = {"clips": {"clip_01": {"severity": "fail", "tier": "must_fix",
                                    "identity_ok": False, "identity_issue": "人脸与角色卡相似度 0.10 低于阈值 0.40",
                                    "hard_gate": {"kind": "face_match", "score": 0.1, "threshold": 0.4}}},
              "flags": ["clip_01 hard-gate"], "feedback": {"clip_01": "人脸与角色卡不符,重拍"}}
    (ep / "episode_review.json").write_text(json.dumps(review), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["retier_reviews.py", "--novel-dir", str(novel), "--apply"])
    assert retier_reviews.main() == 0
    out = json.loads((ep / "episode_review.json").read_text(encoding="utf-8"))
    row = out["clips"]["clip_01"]
    assert row["tier"] == "must_fix", "retier 不得把硬 gate 的 must_fix 降级"
    assert out["feedback"].get("clip_01"), "retier 不得丢掉硬 gate 行的重拍指令"
```

注:测试里 judge_clip 若会被真实调用,monkeypatch 掉(返回 pass  verdict);fixture 只钉 gate 行为。

- [ ] **Step 2: 确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_gate_integration.py -v`
Expected: FAIL(`_face_best_similarity` 不存在 / gate 未触发)

- [ ] **Step 3: 实现**

`episode.py` 主循环中、`judge_clip` 之前插入:

```python
if os.environ.get("NOVEL_FACE_GATE") == "1":
    char_refs = [r for r in clip.get("references", []) if r.get("role") == "character"]
    if char_refs and verdict_is_current_take:  # 与 :86-105 的 take 复用判断同一条件
        score = _face_best_similarity(video=video, refs=char_refs, novel_dir=novel_dir)
        threshold = float(os.environ.get("NOVEL_FACE_GATE_THRESHOLD", "0.4"))
        if score is not None and score < threshold:
            # 按 review schema 同构写 must_fix(字段清单见计划接口段),并 continue 跳过 judge_clip
```

`_face_best_similarity` 是模块级函数,内部:惰性单例 `FaceGate`(模型目录取 `NOVEL_FACE_MODEL_DIR`,默认 `/mnt/disk1/zengzhitao/models/faceswap/antelopev2`)、用 `evidence.clip_frames(video, tmp, 4)` 抽帧、卡片路径 = `novel_dir / r["path"]`。**任何异常(模型缺失、cv2 失败)都 catch 并返回 `None`**(fail-open,gate 永不让审片崩)。

`retier.py` 主循环(:70-73)加豁免——**不能简单 `continue`**(flags/new_feedback 是按遍历重建的,跳过会让硬 gate 行的 flags 和 feedback 被静默丢掉);正确改法是让 tier 保留原值、下游逻辑照常走:

```python
# retier.py:73 原: tier = fix_tier(verdict, bible, rules)
tier = verdict["tier"] if verdict.get("hard_gate") else fix_tier(verdict, bible, rules)
# retier.py:74 的 script_check 条件追加: and not verdict.get("hard_gate")
# (硬 gate 的投诉是"脸不像",不是剧本疑点,script_check 对它无意义)
```

`configs/pipeline.json` 加 `"face_gate": {"enabled": false, "threshold": <B2 校准值>, "model_dir": "/mnt/disk1/zengzhitao/models/faceswap/antelopev2"}`(默认关,按书开);同步刷新 `tests/fixtures/runtime_config_before.json`。

- [ ] **Step 4: 测试 + 全量回归 + 单集实测**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_face_gate_integration.py -v`
Expected: PASS
Run: `PYTHONPATH=src .venv/bin/python -m pytest -x -q`
Expected: 全绿
实测:挑光明纪元一集已验收集,`NOVEL_FACE_GATE=1 NOVEL_REVIEW_FRESH=1 PYTHONPATH=src .venv/bin/python scripts/thin_review.py episode --episode-dir <dir>`(先备份该集 episode_review.json),确认 gate 不炸、不误报;恢复备份。

- [ ] **Step 5: Commit**

```bash
git add src/novel_manga/application/review/episode.py src/novel_manga/application/review/retier.py configs/pipeline.json tests/fixtures/runtime_config_before.json tests/test_face_gate_integration.py
git commit -m "review: 人脸相似度硬 gate 接入审片(默认关,按书开;retier 豁免)"
```

---
