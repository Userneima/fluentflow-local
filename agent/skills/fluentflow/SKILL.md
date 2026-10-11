---
name: fluentflow
description: Turn a video, recording or video link into a transcript and a study note with the local FluentFlow app, and improve how notes are written from the user's feedback. Use when the user gives a video or audio file, a Douyin, Bilibili or YouTube link, or a folder of recordings and wants it transcribed, summarised or made into notes; when they send a screenshot of a FluentFlow note or say a note is wrong, too long, missing something or written the wrong way; and when they ask what FluentFlow did with a recording. Not for editing a video, and not for notes on written articles.
---

# FluentFlow

FluentFlow runs on the user's machine and does the heavy work: download, cut silent gaps, transcribe, pick frames. You write the note yourself from what it hands you, so the user needs no model key. All of it goes through the `fluentflow-local` MCP tools; do not reimplement any step in the conversation.

If the tools fail because nothing answers at `127.0.0.1:8000`, ask the user to open "FluentFlow Local" from the Desktop (or start it yourself the same way), then retry.

## Making a note

1. Submit: `submit_local_media` for a file path, `submit_video_link` for a link. One call per recording; a folder is several calls.
2. `wait_task` until the task is done. Transcription takes a few minutes per hour of recording on Apple silicon.
3. If the task already has a note (the user configured a model key in FluentFlow), use it. Otherwise call `get_agent_note_material`, open the frames you need, write the note following `instructions` exactly, and save it with `save_agent_note`. A long recording comes in `total_parts` parts: fetch and write each, save them together with `part_notes`.
4. Put the note where the user reads it: `export_result` with `target=pdf` (a PDF with its screenshots, in ~/Documents/FluentFlow 笔记 unless they said otherwise), or `target=lark` when they read in Feishu. Tell them in one or two sentences what the recording was and where the note is. Do not paste the whole note into the conversation unless asked.

## When the user says a note is wrong

The user's job is to point; checking is yours. Keep the user's effort to the screenshot and a sentence.

1. Find the note and the spot. Fix this note now and save it with `save_agent_note`; this happens for every report.
2. Decide whether it is a one-off (a misheard name, one wrong number) or a way of writing that would come back in other notes (summaries tacked on, qualifiers dropped, headings too deep). Record it with `record_note_feedback`: `kind=one_off` or `kind=pattern`, the user's words as `problem`, and for a pattern a `criterion` a checker can apply by reading the note alone or with the transcript (for example "the note keeps 往往 where the speaker said 往往").
3. One-off: done. Tell the user it is fixed.
4. Pattern: call `list_note_feedback` and judge whether this problem was reported before.
   - **First time**: read the current rules with `get_note_skill`, draft the smallest change that would prevent it, merged where it belongs in the rules rather than appended. Rewrite the part of the recording where the problem was, with the draft rules, three times (yourself from `get_agent_note_material`, or with `draft_note_part` when FluentFlow writes the user's notes). For each rewrite, ask a separate checker that did not write it (a fresh subagent given only the criterion, the rewritten part and the transcript excerpt) to answer pass, fail or cannot tell. Also rerun the earlier pattern cases in `list_note_feedback` that the change could affect, the same way. If anything fails, revise the rule and repeat; stop after a few rounds and tell the user what you tried if it still fails. When every rewrite passes, call `apply_note_rule` with the whole new rules text, the rule in one sentence, the feedback ids, the number of rounds and what was checked. Tell the user one sentence: "以后的笔记会……（加了一条规则）", and that it can be undone in 设置 → 我的笔记风格.
   - **Reported before**: the existing rule did not work. Show the user the rule as written now and a before/after of the problem part, ask how they want it handled, and change it only after they agree.
5. Never write a rule the checker has not passed, and never ask the user to review a rule that is being added for the first time.

## What not to do

- Do not change the product's default rules; `apply_note_rule` writes the user's own copy.
- Do not judge your own rewrite; the checker exists because the writer is the worst judge of whether its fix worked.
- Do not delete tasks, transcripts or notes; restoring an earlier note is `write_note_from_cut_media` with `restore_previous_note=true`.
