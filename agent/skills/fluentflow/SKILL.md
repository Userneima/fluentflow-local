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
3. If the task already has a note (the user configured a model key in FluentFlow), use it. Otherwise call `get_agent_note_material`, open the frames you need, write the note following `instructions` exactly, and save it with `save_agent_note`. A long recording comes in `total_parts` parts, each small enough to read in one go: fetch, write and save one part at a time (`save_agent_note` with `part`). Saved parts stay in FluentFlow, so if the conversation is compacted or a new one starts, carry on from the parts the answer lists as remaining; the task's note is replaced only once every part is in. The answer's `coverage.missing` lists knowledge points FluentFlow's check found that part never mentions; when it is not empty, write those points in and save the same part again once. When the last part is in, do what the answer's `next` asks: read the saved parts back one at a time with `get_agent_note_part`, merge conclusions and term explanations that repeat across parts, and save again only the parts you change.
4. Put the note where the user reads it: `export_result` with `target=pdf` (a PDF with its screenshots, in ~/Documents/FluentFlow 笔记 unless they said otherwise), or `target=lark` when they read in Feishu. Tell them in one or two sentences what the recording was and where the note is, and, for a file from their own folder, the folder beside the recording that holds every output (`output_folder.path`: the shortened or compressed video, the PDF note, and in 过程文件 the transcript, subtitles and markdown note); FluentFlow never moves or deletes the original. Do not paste the whole note into the conversation unless asked.

## When the user says a note is wrong

The user's job is to point; checking is yours. Keep the user's effort to the screenshot and a sentence.

1. **Find the task.** Read the note's title off the screenshot and search a few characters of it with `list_tasks` (`query`); it matches the note's own title as well as the file name. Pick the one whose `note_title` matches. Only when nothing matches, ask the user for the video link or file name, nothing else.
2. **Fix this note now**, for every report: read it with `get_task_package`, correct the spot, save the whole note with `save_agent_note` (`note_markdown`, no `part`), then deliver it again the way the user reads it (`export_result`, `target=pdf` or `lark`) so their copy is the fixed one. Note who wrote it before you touched it: `cut_media_note.channel` in the package is `agent` when an agent wrote it, anything else means FluentFlow's own model did.
3. **Decide the kind and record it** with `record_note_feedback`: `kind=one_off` for a single error (a misheard name, one wrong number), `kind=pattern` for a way of writing that would come back in other notes (an intro section nobody needs, summaries tacked on, qualifiers dropped). Pass `task_id`, the `part` the problem is in (see below), the user's words as `problem`, and for a pattern a `criterion` a checker can apply by reading the rewritten text alone or with the transcript ("the note starts its content right under the title, with no section introducing the lecture"). The answer names the frozen transcript of that part in `transcript_excerpt_file`; keep the path for the checker.
4. One-off: done. Tell the user it is fixed.
5. Pattern: call `list_note_feedback` and judge whether this problem was reported before.
   - **First time**: read the current rules with `get_note_skill` and draft the smallest change that would prevent it, merged where it belongs in the rules rather than appended. Then verify:
     - **Rewrite** the part where the problem was three times with the draft rules. A *part* is what `get_agent_note_material` returns: a short recording is one part (`part=1`); a long one comes in `total_parts`. Each rewrite is written by a fresh subagent given only that part's material with the draft rules in place of the current ones, so the three are independent. When FluentFlow's own model wrote the note (step 2), use `draft_note_part` instead, so the rule is verified on the writer that will use it.
     - **Check** each rewrite with another fresh subagent that wrote none of them, given only the `criterion`, the rewrite and the file at `transcript_excerpt_file`, asked to answer pass, fail or cannot tell.
     - **Rerun earlier cases**: every earlier `kind=pattern` entry in `list_note_feedback` whose rule the change touches gets one rewrite (from its own `task_id` and `part`) and one check against its own `criterion`.
     - Any fail: revise the rule and repeat. After three rounds that still fail, stop and tell the user what you tried.
     - All pass: call `apply_note_rule` with the whole new rules text, the rule in one sentence, the feedback ids, `rounds` = the number of rewrites that passed the checker, and what was checked. Tell the user one sentence, "以后的笔记会……（加了一条规则）", and that it can be undone in 设置 → 我的笔记风格.
   - **Reported before**: the existing rule did not work. Show the user the rule as written now and a before/after of the problem part, ask how they want it handled, and change it only after they agree.
6. Never write a rule the checker has not passed, and never ask the user to review a rule that is being added for the first time.

When subagents are not available to you, do the rewrites and checks one at a time in separate, clean contexts as far as your host allows, and say in `evidence` that the writer and checker were not separate.

## What not to do

- Do not change the product's default rules; `apply_note_rule` writes the user's own copy.
- Do not judge your own rewrite; the checker exists because the writer is the worst judge of whether its fix worked.
- Do not delete tasks, transcripts or notes; restoring an earlier note is `write_note_from_cut_media` with `restore_previous_note=true`.
