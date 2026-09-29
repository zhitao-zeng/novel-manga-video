---
name: video-repair
description: Diagnose a failed video clip from source, requests, images, actual takes and review history, then propose a bounded final H3 request correction.
---

Read input/evidence.json, current prompt, source shot and neighbouring shots, reference images and current/prior takes. Compare the actual picture with the intended shot; do not merely repeat the reviewer. A positive reference check is not proof of correct plot or object counts. Treat proposed generation causes as hypotheses unless supported by a direct input contradiction or controlled evidence; an omitted descriptive phrase alone does not prove why the model erred. Category changes can describe the same visible fault.

Return output/decision.json with action (edit, retake, keep, stop), reason, and evidence (a list of input file paths plus concise observations). For edit also write output/prompt.txt, the complete final H3 English request. Replace conflicting text rather than appending the error description. Correct wearing versus independent objects, visibility and physically observable states using the source and actual references; do not invent a new scene. If the source itself is wrong and cannot be faithfully expressed, choose stop and explain the required source change.

Preserve the six H3 sections, the exact subject_definitions, retention labels/modes and reference mappings, exact dialogue and speaker/audio bindings, shot count and timing. You may clarify clothing/state descriptions in retention_analysis using the reference evidence, and revise shot directions and audiovisual corrections, but not add dialogue, change story events or change the scene's factual order. Speech directions can specify when the existing utterance starts/stops and who stays silent; they are not new dialogue or a reason to invent visual content.

retake means the input is already correct and one new seed is justified. keep proposes a false-positive recheck; the pipeline must independently verify it. stop means there is no supported repair. Do not force an edit for every clip.

Only work on /work copies. Do not call generation services, modify budgets, publish or attempt to access production paths. The pipeline owns actual generation and its existing total-attempt cap. No automatic extra retry follows a rejected candidate. Do not infer electrical sound causes visible arcs: that hypothesis was not supported by the controlled examples.
