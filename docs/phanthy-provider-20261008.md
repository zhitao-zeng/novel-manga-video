# GPU16 Phanthy provider — 2026-10-08

`phanthy` is a peer of `deepseek` and `flashnext`. The selected model is
`glm-5.3-flash`; the authenticated model list also contains `glm-5.3`, which was
not selected for this integration.

## Runtime configuration

- Private credential: `/mnt/disk1/zengzhitao/novel-manga-video/.env`, variable
  `PHANTHY_API_KEY`, file permission0600, ignored by Git. It was read from the
  user-supplied `~/.config/phanthy-local-api/api-key`. No OAuth/login files changed.
- Model resource: runtime `configs/pipeline.json`, name`phanthy`, shared limit1.
  Existing resource settings and novel allocations were preserved.
- Agent config: runtime `configs/agent_phanthy.json`; its public counterpart in
  this worktree contains only the key variable name, never a credential.
- OpenAI Chat Completions base: `http://127.0.0.1:8789/v1`.
- Native Agent base: `http://127.0.0.1:8789`. Actual POST/messages and sandbox
  tool calls passed, so no new protocol-conversion service was needed.

Use the updated `feat/episode-agent` code with
`NOVEL_PROJECT_ROOT=/mnt/disk1/zengzhitao/novel-manga-video`; the runtime root holds
the private configuration. The model preset selects streaming Chat Completions,
low reasoning effort and no minimum output-token floor. DS/Qwen vLLM presets
keep their existing non-streaming behavior.

The local API listens on loopback. Only this Agent config requests Docker host
networking so127.0.0.1 reaches GPU16 rather than the container itself. Both the
generic sandbox and episode-session launchers support that provider-specific
setting. Authentication is passed through environment inheritance, not command
arguments or saved attempt metadata.

GLM Flash rejected a thinking-disabled request during validation. It is configured
with low effort instead. A64-token check exhausted its output allowance in
reasoning; the successful ordinary check used512. The JSON client now treats an
empty length-truncated response as truncation and uses its existing single bounded
retry, rather than silently accepting`{}`. It does not raise budgets to50000.

## Actual validation

| Check | Result |
|---|---|
| Authenticated GET/v1/models |200; both requested model IDs listed |
| Ordinary Chat Completions |`PHANTHY_OK`,7.85s |
| Native POST/v1/messages |`PHANTHY_AGENT_OK`,9.85s |
| Project JSON client |Expected`ok=true`/`PHANTHY_JSON_OK` object,9.25s |
| Synthetic blue image |Answered`深蓝色`,12.35s |
| Chat Completions tool loop |Model called project status; host returned clip_count2; final JSON used2,10.633s+4.93s |
| Existing sandbox episode Agent |Bash invoked project status through the real socket; Agent wrote the expected JSON and returned`PHANTHY_AGENT_TOOL_OK`; exit0,31.346s |

The tool checks used synthetic episode files and a status-only host bridge. They
performed no rendering, actual novel repair, or media generation. The native
Agent was limited to4turns,180s and1024output tokens per request for this check;
these test-only limits were not applied to the production provider config. Its
attempt artifacts were checked for the actual API key and contained none.

One initial standalone Chat tool check failed JSON parsing without retained
stage detail. A diagnostic rerun completed the full roundtrip; its reason cannot
be retroactively asserted. These checks establish integration, not a success-rate,
concurrency-capacity or whole-episode throughput benchmark.

Sanitized evidence: runtime `.runtime/model-providers/phanthy/` and this
worktree's `.superpowers/sdd/2026-10-08-phanthy-provider/live-summary.json`.

Regression verification: 107 targeted tests and the full suite of 2794 tests
passed (0 failures, 0 errors, 0 skips). One independent final review found no
Critical or Important issues. Its minor configuration-comment correction was
applied: the supported private file is the runtime project's `.env`, not
`private.env`. The same correction is present in both copies of the Agent config.

## Prepared production option

The current400chapter run remains stopped. A separate, unactivated configuration
is saved at
`.runtime/guangming-401-800-20261006/repair-plan-20261008/pipeline-with-phanthy.json`:
DS8 + Qwen4 + Phanthy1, with existing H3 limits and chapter budgets retained.
The previous launch pointer still names V15; it was not changed. This prepared
configuration requires the updated feature-branch code before use.

No existing model service or OAuth proxy was restarted, no new model/dependency
was installed, and no commit/push was made for this integration.
