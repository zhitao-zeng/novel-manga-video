---
name: episode-production
description: Author, execute and repair one complete novel episode using original text, actual assets, media evidence and the supplied host workflow client in a persistent episode workspace.
---

# Episode production

Read `input/manifest.json` to identify the input mode and its authority.
In source mode, own this episode's screenplay, storyboard and final H3 requests as one author.
In script mode, retain the supplied screenplay, clip groups, exact dialogue/ownership,
camera, durations and selected references; author only the final H3 requests in
`output/requests.json`. Source contradictions are reported for a supported correction.
Read the complete original chapter and inspect the supplied actual asset images.
Preserve the source's key causes, identities, action ownership, motivations and decisions.
In source mode choose the cinematic cuts, framing, acting and rhythm yourself.
Compress or rewrite dialogue while preserving its meaning and who knows, thinks or says each fact.
Record meaningful adaptation choices and verbatim source evidence for each shot.

Use the supplied asset identifiers and images for stable appearance, locations and
objects. Make each image's role and current visible state clear in its H3 definition.
Keep spoken dialogue, audience-only inner voice and physical sound sources distinct.
Write each final H3 request in full: subject_definitions, summary, retention_analysis,
detailed_description, overall_soundscape and non_diegetic_music. Use the supplied
H3 format reference for numbered pictures, audio, subjects, speakers and retention values.
Describe camera cuts and the timing of action and dialogue in the shot body.
Write production prose in affirmative English; quote chosen Chinese dialogue exactly
inside its language-tagged dialogue marker. Empty audio sections may use `None.`.
Keep reference tags consistent with the actual ordered references selected for the clip.

Use the supplied target duration and clip duration limits. Source mode normally
targets 120–150 seconds; script mode retains the saved episode and clip durations.
Every ordered chapter shot belongs to exactly one clip; a clip may contain several shots.
Set each shot's `duration_seconds` so its clip's shot durations sum to `request_seconds`.
Read [the output contract](references/output-contract.md) and write the requested JSON
file under `output/`. The host supplies the original, asset manifest and format references.

When resumed, keep working on this same episode using its actual frames, ASR,
submitted requests and current verdicts. Inspect the frames before deciding which
clips to keep, edit, retake or stop. Explain each decision using current evidence.
An edit contains the entire replacement H3 request, including all six sections.
Use the episode context to keep changes coherent with adjacent clips.

When Bash and `/work/tools/episode.py` are available, execute this episode yourself.
Use `python3 /work/tools/episode.py --help` for command arguments. Run `check`, read
its errors, edit the mode's requested output file, and check again. After it passes,
run `render`, use `status` to wait for completion, then run `review`. Read the actual
frames, ASR and current verdicts. Update your own episode requests and use `apply`
for repair decisions, then render and review the required clips within the supplied
remaining budget. Run `finish` when the episode is accepted or must stop, and report
the actual outcome. Temporary helper scripts may live inside this episode workspace.

The workspace client reaches the existing host pipeline through its Unix socket.
The host owns format/source checks, H3 services, frame/audio extraction, independent
acceptance, budgets, caches and publication. Use its reported results and allowances;
keep your work and helper scripts scoped to this episode. With file tools alone,
return the requested JSON for the host to execute.
