# Bounded lessons from observed failures

Read a case only when it matches the current problem. These are observations
and supported decisions, not universal prompt templates or quality guarantees.

| Observed case | What was established | Decision it informs |
| --- | --- | --- |
| A saved shot had an exit/return, while a new binding supplied incompatible opening/ending states and another source passage. | The contradiction existed before H3 generation. | Keep approved-script authority; locate a source/binding conflict before spending a retake. |
| Worn armor was lost at the binding boundary and later described as an independent empty suit. | Missing `wears` and inconsistent field handoff changed the request's physical relationship. | Carry the wearer, wearing relation and separately authored copy distinctly; inspect the actual card rather than fixing a type label alone. |
| Official voiceover wording still produced mouth movement; a silent visual take and a separately recorded/mixed thought were observed to work in tested clips. | Format compliance alone did not close the mouth; the postmix route separated visual acting from audience-only speech. | Use the host's single-thinker postmix contract and verify the visual, recording and mix; preserve camera and private meaning. |
| A complete scene image introduced a listener into close-ups intended to exclude them. Ordinary character/room references removed the listener in two tested seeds; another offscreen object still appeared in one. | The wider reference's contents could carry into the crop; the small test did not establish a general solution. | Choose references whose actual content suits the authored visibility and verify the current frame. A scene image is not an automatically valid first frame. |
| Fixed-input old/new prompt comparisons and a hand-written official-style example still showed visual anomalies; some ASR passes missed short planned words. | The tested rewrite did not demonstrate stable improvement or a unique sound/seed cause; automatic passes did not prove full delivery. | Retain the actual request, take and audio evidence. Neither an electrical sound phrase, its removal, a seed change nor formal compliance is a proven universal cure. |
| A fixed 15-second clip contained 217–344 Chinese dialogue characters and repeatedly produced missing/garbled speech. | Input speech volume exceeded the shot duration; fixed-word prompt edits could not fix it. | Use the supported source compression/splitting path before another take. Preserve speaker, causal conditions and essential meaning; aim around 4–5 Chinese characters per second. |
| ASR rendered 箭 as 剑 and character names as matching Mandarin syllables. | Raw spelling disagreement alone did not establish missing audible words. | Use phonetic re-evaluation and inspect audio; do not regenerate a correct take to satisfy spelling. |
| A current female phase image and source were reviewed against an old male base field. | The review input contradicted its current evidence. | Prefer the selected source-backed phase; correct/recheck the verdict before changing the video. |
| Final publication named a bad clip while the ordinary repair list was empty. | A host routing gap hid an unresolved publication target. | Current publication blockers are repair targets too; use the actual per-clip permissions. |
| A source actor needed a card; another already had a card but needed a new appearance. | Missing identity and a different phase are distinct operations. | Request characters for missing actors (including speaking animals); use phases/reuse_asset_id for an existing identity. |
| A local scene correction touched a shot shared by adjacent clips. | Repair scope must include the dependency; repeated identical resumes did not change it. | Use the returned dependent clip/shot scope and repair their source-supported states together. |
| A DS repair turn explicitly refused a fictional chapter and submitted no operation. | The model misclassified the task; more identical DS turns made no progress. | The host may switch once to configured Qwen within the same operation/take budget. Never reset history. |
| A batch of400 chapters had all source text imported and jobs queued, but no new chapter reading, cross-chapter merge or story ledger. | Per-chapter source drafting was being mistaken for complete book preparation. | Check actual reading/roster/story coverage before batch scripting. Continue the established book preparation stages first; source files and queued jobs are not proof of completed reading. |

Do not end an unfinished operation with another conversation-history summary.
The host already retains the workspace and current diagnosis. On a fresh model
handoff, inspect the current draft/status and named unresolved evidence first;
reuse existing output rather than rebuilding it or rereading all old attempts.
Before adoption, asset-input check addresses the draft's clip_01, clip_02, etc.
Use `python3 /work/tools/episode.py check output/repair.json` (positional path).

Local preparation lessons from the Guangming batch (2026-10-06): a location
finding must not become a speaker re-attribution merely because its evidence
mentions speech. Correct its recorded kind through the native input tool. A
source-described offscreen extra keeps its real identity without a new portrait;
`speaker_map` changes ownership, not dialogue or timing. Missing pictures or one
invalid English request do not justify rewriting all shots. The host only uses
source authoring for a missing/empty plan or a confirmed whole missing event.
Whole-book location merging can coarsen rooms into a hotel name: consult the
chapter reading's actual physical spaces; never move a bedroom action into a
lobby because the lobby already has a picture.

The host supplies current diagnosis and a small set of prior observed outcomes on
each turn. An unresolved prior attempt is evidence of what failed, not a recipe.
Use new failures to choose a different supported remedy. Do not wait for a human
to restate these lessons or to tell you which evidence files to inspect.

Source records maintained by the project:
`docs/authored-binding-prompt-simplification-20260929.md`,
`docs/production-lessons.md`, `docs/prompt-production-repair-20260930.md`,
`docs/prompt-chain-consolidation-20260930.md`, and
`docs/h3-runtime-audit-20260924.md`. These document paths identify provenance;
the necessary lessons above are supplied locally in this skill, without importing
historical run logs or their old operational instructions into the sandbox.


## 准备阶段失败不是停止理由

出现`input/preparation/exception.json`时，普通规划或准备已经失败过；先读它列出的旧稿、原模型候选及审核证据，保留有效创作选择，针对原因修复。未开拍的失败稿不是定稿，允许在source草稿内修正编剧、分镜和绑定，不能重复普通编稿后丢掉失败原因。

`input/reading/locations.json`保留本章读书发现的场景；素材库没有图片，不表示故事发生在另一个有图的地点。海底、海滩、山崖不能因为图库只剩大学城就改成大学城。需要图片时先把真实角色/地点写进草稿，再用现有`apply action=input`的characters/locations提交原文依据、设计与补图请求。实际检查图片并绑定后继续check/render，不以文字声称素材已完成。

公共故障经验：H3的Subject不是人类分类，动物不能被模板默认写成人。一次局部修复若报告其他片段改变，先区分真实姿态继承与编译器重算造成的漂移；宿主会保留未改前缀、自动处理有证据且未越预算的依赖，不需要通过扩大全部章节范围反复碰运气。若真实依赖涉及已拍片段或用完预算，保留准确原因，不改次数。共享素材复核结果可以复用，但needs_review不是已通过；未成功的旧修法是失败证据，不是成功案例。
