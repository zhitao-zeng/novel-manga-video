# H3 format for this host

The bundled [official full-reference guide](official/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)
is the MiniMax upstream document, unmodified, from
`MiniMaxAI/MiniMax-H3` revision `5d9b308a59ab12e67147f191e184baf704185bd1`.
[Primary source](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/5d9b308a59ab12e67147f191e184baf704185bd1/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md).
Retrieved 2026-10-02; 23,553 bytes, with the guide title and complete six sections
checked. Its [license](official/LICENSE) and [notice](official/NOTICE) accompany it.
Read the official guide for reference roles, retention or sound syntax; this
page supplies the current host's narrower interface and project policy.

## Numbering and roles

Write these headings, each on its own line, in order:
`subject_definitions`, `summary`, `retention_analysis`, `detailed_description`,
`overall_soundscape`, `non_diegetic_music`.
Use English scene prose throughout, with original dialogue inside `<d>[Chinese] ...</d>`.
The final filming instructions use affirmative visible facts and sound relationships;
upstream examples are syntax evidence, not a request to copy their scene or wording.

Pictures and audio are numbered separately in selected reference order. The host
numbers unique character Subjects first in image-reference order, followed by
noncharacter assets. One character's multiple images share one Subject. Subject
labels can also denote a setting, clothing or an object. Define the content and
its actual source pictures; a standalone Picture definition is for a supported
concrete frame anchor, not an ordinary identity, costume or location card.

The retention relationship describes the referenced role, using official visible
markers `fully_preserved`, `partially_preserved`, `attribute_transfer` or `weak_reference`.
Audio relationships use `fully_copy`, `partially_copy`, `reference` or `weak_reference`
when the supplied task supports that use. This host's voice samples ordinarily
provide timbre rather than copied source words. Stable identity/design may be
restated at the first relevant appearance; this does not replay an action.

Start `summary` with the task prefix that matches the sent request, such as
`[reference generation]` for ordinary image references or
`[reference generation + audio reference]` when a voice reference is actually sent.
An audience-only recording mixed later does not make the silent visual request
an audio-reference generation task. The official guide describes broader modes;
use only the roles and conditions the host has actually supplied.

## Timeline and voices

Use clip-local `[Shot 1]`, `[Shot 2]`, etc. and the supplied `Planned duration: X seconds.`.
The first shot starts at zero; later shots use `At MM:SS.mmm,` with cumulative
preceding durations. Put visible behavior, state changes, speaking delivery and
action sounds at their playback positions, keeping the authored camera.

Number `(S1)`, `(S2)`, etc. by the first actual vocal event in the clip. Reuse the
same speaker ID through the clip, independently of Subject or Audio number:

```text
<Audio 1> is the voice-timbre reference for <Subject 2> (S1).
<Subject 2> (S1) says <d>[Chinese] 原样台词。</d>
The speaking voice follows the timbre referenced from <Audio 1>.
```

Place English vocal delivery beside the bound utterance. For an offscreen owner,
use `<Subject N> (Sx) says in an off-screen voiceover <d>[Chinese] ...</d>`;
an owner without a visual Subject uses `An off-screen voice (Sx)` with the same
delivery syntax. Keep words, ownership and stage consistent with structured turns.
Complete utterances appear once in the shot body, rather than repeated in summaries
or sound sections. Shot-specific synchronized sounds belong beside their actions;
`overall_soundscape` summarizes the clip's environmental sound, and
`non_diegetic_music` describes audience-only music. An empty section uses `None.`.

For `inner_voice_delivery: postmix`, the clip has one thinker and only inner-voice
turns. Select the supplied thinker character card and voice for recording. Keep
thought words in structured turns, while the visual H3 request contains zero
native dialogue and zero Audio tags/definitions; the host records and mixes the
thought separately. Its pictured reaction follows the authored crop and camera.
Mixed private/native speech needs explicitly authored separate clips or a host-
supported correction, not an inferred timing or a demotion of private speech.
