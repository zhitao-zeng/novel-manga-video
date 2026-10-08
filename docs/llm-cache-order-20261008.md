# DS / Qwen Agent prefix-cache correction — 2026-10-08

The Agent client adds chronological system messages containing its remaining
budget. Both deployed Anthropic adapters defaulted to moving every inline system
message ahead of the conversation when no explicit template was supplied. Each
new budget notice therefore changed the beginning of a long, otherwise stable
request. This is separate from model switches, context compaction and KV capacity.

## Correction

- Keep inline system messages in their original order, including all budget text
  and ordinary instructions. Existing host budgets and output limits are unchanged.
- DS uses its native `deepseek_v41` tokenizer, which accepts chronological system
  messages. Its adapter now disables front-merging.
- Qwen's default Jinja template rejects late system messages. A derived template
  renders these as system messages through the model's existing `render_content`
  macro, accepting both strings and the content-block lists produced by vLLM.
  Only the Anthropic handler selects it: both
  the handler and a shallow copy of its `OnlineRenderer` use the derived template.
  The shared renderer used by OpenAI endpoints is preserved.
- The already-present billing-header filtering remains unchanged. No budget
  notices were stripped and no system message was converted to a user message.

The independent review found the renderer delegation issue before rollout. The
initial offline tokenizer test bypassed this path; a failing/passing regression
now verifies the handler-local renderer and preservation of the shared renderer.
The first live rollout then found the renderer's string-to-content-block
normalization. That rollout was rolled back. The actual-template regression now
reproduces that failure and verifies identical plain-text/content-block tokens,
including a tool round trip, after the macro correction.

## Files and rollout targets

Installer: `scripts/llm_cache_order_patch.py`. CPU contract check:
`scripts/llm_cache_order_service_check.py`. Bounded synthetic inference probe:
`scripts/llm_cache_order_live_probe.py`. Regression tests:
`tests/test_llm_cache_order_patch.py`.

| Backend | Existing container / endpoint | Prepared state in container |
|---|---|---|
| DS, GPU52 | `4c5bebe8fe70`, port8831 | `/opt/novel-llm-cache-order` |
| Qwen, GPU81 | `bacf634d9bab`, port8038 | `/opt/novel-llm-cache-order-v3` |

Both source changes target
`/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/anthropic/serving.py`.
The state directories hold `before.py`, `after.py`, `prepared.json`, and for Qwen
`qwen-inline-system.jinja`. Earlier Qwen v1 preparations were not installed;
v2 was rolled back after failing live text-block rendering. Use the v3 directory
for the final patch. Shared model files were not modified.
Container identity, authentication, GPU selection, concurrency and model versions
are preserved. The patch survives a restart of these containers. Recreating from
the original image requires running the versioned installer again.

The installer requires the known adapter/template structure and refuses unknown
or changed source. Prepare before applying; keep the rollback snapshot. The
template and installer contain no service credentials.

## Validation and operational limit

Both original adapters fail the chronological-order contract; both candidates
pass with their actual installed tokenizers. Offline old-request prefix retained:
DS3510/3510 tokens, Qwen3535/3535. This is potential reuse, not a cache-hit measure.
Six local regressions and all2729 project pytest tests pass after the final fix.

Pre-rollout Qwen synthetic continuation: original messages endpoint reused
0/10006 tokens (0.818s); candidate preformatted completions reused8800/10030
(0.249s). The output code supplied only in system messages was correct in both.
An earlier adversarial conflicting-user case failed on the original service; this
change does not claim to improve model instruction-hierarchy adherence.

Final v3 deployed Anthropic endpoint: cold first request9972input tokens,1.291s;
continued request1230uncached+8800cached=10030input tokens,0.255s (**87.7% reused**).
The authenticated model identity, unauthenticated rejection, system-only code
retrieval, count_tokens(10030), forced tool call and tool-result-plus-later-system
continuation all passed. Four final inference requests were used. Installed CPU
contracts also passed. These are measured request-level results, not an estimate
of whole-episode throughput; cumulative service counters reset on restart.

DS independently exited before source modification. One restart of its unmodified
container failed with CUDA initialization errors in all six workers. Its adapter
was therefore tested in the identical image with CPU only and read-only model
mounts. The verified source patch was copied to the stopped original container
only after checking its bytes still matched the saved original. There was no
second restart, driver reset, GPU0 intervention or H3 change. DS live cache reuse
remains unverified until the host's CUDA problem is resolved.

Update after the user's “DS重新接上了”: the same six-GPU container is healthy and
retains the installed patch. Its real Anthropic endpoint passed three warmed
continuations, each reusing8896/8946 input tokens (**99.4%**), with latencies0.425,
0.296 and0.309s. Three initial requests each created8896cache tokens, consistent
with the three independent replicas requiring separate warmup. Model identity,
authentication, count_tokens(8946), a tool call and a tool-result continuation
passed. Eight bounded synthetic inference requests were used; no service restart
or episode production was performed in this verification. Evidence is in
`cache-fix-20261008/ds-reconnected/live-probe.json`. The earlier CUDA-blocked
verification remains a historical record, superseded by this live result.

Evidence and final live results are under the batch runtime:
`/mnt/disk1/zengzhitao/novel-manga-video/.runtime/guangming-401-800-20261006/cache-fix-20261008/`.
Synthetic text timings do not establish chapter throughput. Book runs, retries,
take budgets and acceptance thresholds were not changed.

## Rollback / reapplication

After active and waiting requests have drained, run the saved installer's rollback
inside the exact container and restart that container. For Qwen:

```sh
sudo docker exec bacf634d9bab python3 \
  /opt/novel-llm-cache-order-v3/llm_cache_order_patch.py rollback \
  --state-dir /opt/novel-llm-cache-order-v3
sudo docker restart -t 30 bacf634d9bab
```

For DS use its container and `/opt/novel-llm-cache-order`. When stopped, compare the
container source with `after.py` before restoring `before.py` through `docker cp`.
Do not overwrite a later unrelated operator edit. Restoring adapter source removes
the template selection; leaving the unused derived template has no runtime effect.

For a recreated compatible container, copy the current installer into an owned
directory, run `prepare --backend ds` or `prepare --backend qwen` with the intended
`--state-dir`, run the candidate CPU check, then `apply` and restart after drain.
Verify live messages, count-tokens, a tool round trip and actual cache-read counters;
an offline common-prefix comparison alone is insufficient.
