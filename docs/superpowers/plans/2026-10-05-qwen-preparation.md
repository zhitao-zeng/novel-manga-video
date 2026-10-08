# Qwen preparation routing

Approved brief: use the existing GPU81 Flash Next service for preparation at two
concurrent requests, keep DS as episode controller/final judge, evaluate actual
saved failure inputs before promotion. Preserve all video/input/recheck budgets.

Evidence: eight real saved request/reference cases and four translation inputs
were compared using the same production functions, frozen reference images and
two workers per endpoint. Three translation inputs are supported by the existing
translator; both models preserve their protected contracts. The fourth is a
source-agent draft without packed Chinese stages and is not a model failure.
Reference decisions disagree on two robe examples; do not promote that gate on
this small and disputed set. No production content or cache was edited by probes.

Implementation:

- Add separate named endpoint overrides for H3 translation and request/reference
  checks, with unchanged legacy defaults when absent.
- Pass optional task settings through episode runtime_environment; register
  secondary endpoints in the existing shared model-slot pool at two requests,
  preserving DS capacity, Agent routing and final judge settings.
- Record the selected translation model in the existing diagnostic stream.
- Tests must prove independent routing, unchanged defaults, shared secondary
  capacity and no mutation of the parent process environment.
- Run focused regressions and the full suite, one scoped independent review,
  then enable translation-only Flash Next in this book's task configs. Do not
  restart running authors or change their time/take budgets.
- Verify an actual worker's configuration and a production translation call;
  keep any unobserved layer explicit. Save probe evidence and rollout state in
  the existing runtime qwen-preparation-20261005 task directory.

H3 recovery: user reports the GPU81 instance restored. The task pool already has
it enabled, its latest ten requests completed, and an existing new output probes
as960x544 H264/AAC. Preserve that recovered state; no restart or extra video test.

No commits, pushes, environment installs or manual per-shot edits.
