# First use (第一次使用): walking a new user through one real task

Read this when `get_status` returns `first_use.should_onboard=true`, or the user says
「带我用一次 FluentFlow」. Say the lines below as written (the user's language is
Chinese here); `{…}` is replaced with the real value. Every step the user answers
with one line or one number; you do everything else.

## Rules for the whole walk-through

- Record as you go with `record_first_use`, so a later conversation picks it up: `need`
  right after question 1 (the number, or their words for 6), `material` right after
  question 2 (the path or the link), `done=true` once step 5 is delivered.
- `first_use.need` / `first_use.material` already set (the install conversation asked):
  do not ask again; go on from the first step that is missing.
- The user skips the questions and just gives a file or a link: do not stop them. Record
  it as material with need 1 for a recording or a lesson, 2 for a Douyin, Bilibili or
  YouTube link, and go on to step 4.
- They say 「不用教了」 (or anything like it): `record_first_use(done=true)`, stop the
  walk-through, and just do what they ask.
- Count the user's messages in this conversation from their first one up to the
  delivery: that is the N of 「整个过程你只打了 N 次字」.
- Between the expectation line (step 4) and the delivery (step 5) say nothing: judge
  the cut, correct the transcript, write and check the note, align it, all without
  asking the user. The usual rules of the skill apply to each of those.

## Step 1: what they want most

During the install (the install conversation, `AGENT_INSTALL.md` "First use"), start with
「我在后台装 FluentFlow 了，你不用管它。趁这会儿先问一句：」;
in a new conversation start with 「先问一句：」. Then:

> 你装它最想先解决哪件事？回个数字就行。
>
> 1. 网课、讲座视频，变成能跟着学的笔记（带截图、分章节，点一下就跳回视频那一段）
> 2. 抖音、B 站、YouTube 收藏的视频，变成能直接看的文字笔记
> 3. 会议、访谈的录音录像，变成逐字稿和整理好的纪要
> 4. 只要逐字稿或字幕
> 5. 剪掉录音里的空白和气口，顺便压小体积
> 6. 其他（说说你的情况）

## Step 2: the material

By their choice:

- 1: 「好，那就做一份能跟着学的笔记。把视频文件拖进来，或者贴网课链接。手上没有的话，打开抖音随便找一条讲东西的视频，复制分享链接贴过来就行。」
- 2: 「好，直接贴链接就行，不用先下载。把想看的抖音、B 站或 YouTube 分享链接发过来；手上没有的话，打开抖音随便挑一条复制链接。」
- 3: 「好，我会把不同的人说的话分开，再理一份纪要。把录音或录像文件拖进来。手上没有会议文件的话，先找一条访谈类的抖音视频，复制分享链接贴过来试试。」
- 4: 「好，这次只出文字稿和字幕，不剪视频、不写笔记。把文件拖进来，或者贴一条抖音、B 站、YouTube 链接。手上没有的话，抖音随便复制一条分享链接。」
- 5: 「好，这次把没人说话的空白和气口剪掉，能压小的也顺便压小。把录像或录音文件拖进来。手上没有的话，找一条说话慢、停顿多的抖音视频，复制分享链接贴过来。」
- 6, something it does: 「这个能做，我会{一句话说怎么做}。把{文件或链接}发过来就行。」
- 6, something it does not (the list at the end): give that item's answer, then 「想试前面几种的话回个数字，或者直接发个视频、抖音链接过来。」

Material received while the install still runs: 「收到。等装好我就直接开始处理这条，你不用再发一遍。」

## Step 3: installed, start at once

An agent that can run commands (Claude Code, Codex, OpenCode) asks the user for nothing
here: it goes straight to step 4, calling the tools from the command line in the install
conversation (`AGENT_INSTALL.md`).

An agent that cannot call the tools in this conversation (a desktop app, an editor
plugin) records need and material if it can reach `record_first_use` at all (the command
line, see `AGENT_INSTALL.md`); otherwise the new conversation finds them empty and asks
again. Then:

> 装好了。还差一步：工具是刚装上的，要开一个新对话才认得它。你新开一个对话，发一句「带我用一次 FluentFlow」，我会接着处理你刚才给的那条，不用再发一遍。

## Step 4: submit, one expectation line, then silence

Submit by their choice, with the skill's defaults otherwise:

- 1, 2: `submit_local_media` (a path) or `submit_video_link` (a link), as for any note.
- 3: the same with `speaker_diarization=true`.
- 4: `cut=false`; write no note: deliver when `wait_task` returns `done=true`.
- 5: the default cut; write no note: deliver when `wait_task` returns `done=true`.
- 6: whatever you told them in step 2.

Then one line. `get_status` `first_use.speech_model_ready=false`, or a `wait_task` answer
with `task.downloading_model=true` before you said it (then say this line once, then):

> 开始处理「{标题或文件名}」。这是装完的第一条，电脑还在下语音识别要用的文件（一两个 G），这次会多等几分钟，往后就快了。不用守着，电脑别合盖就行，好了我叫你。

Otherwise:

> 开始处理「{标题或文件名}」，大概要 {几分钟}。不用守着，电脑别合盖就行，好了我叫你。

## Step 5: deliver against their choice

`{输出文件夹}` and `{page_url}` come from the task (`wait_task`'s `task`, `page_url`). End
every delivery with 「整个过程你只打了 {N} 次字。」 Then `record_first_use(done=true)`.

- 1: 「做好了。按讲的顺序分好了章节，插了关键画面的截图，每段都对好了视频时间。笔记在这里：{输出文件夹}（有 Markdown 和 PDF 两份）。想边看视频边对着笔记：{page_url}，点右边任意一章就跳回视频那一段。」
- 2: 「看完了，这条讲的是：{两三句重点}。带截图的完整笔记在这里：{输出文件夹}，PDF 那份可以直接发给别人。想边播原片边对着看：{page_url}」
- 3: 「整理好了，每个人说的话分开标了，也理出了纪要。纪要在这里：{输出文件夹}，完整逐字稿在里面的「过程文件」文件夹。想边听录音边核对原文：{page_url}」
- 4: 「转好了，原片没剪，也没写笔记。文字稿和字幕文件（txt、srt、vtt）在这里：{路径}。想边播边改错字：{page_url}」
- 5, with a compressed copy (a video on a Mac): 「剪好了。原来 {原时长}，剪掉 {N} 处空白和气口，现在 {新时长}。剪好的版本和压小的原片副本都在这里：{输出文件夹}，原片没动。想看具体剪了哪几段：{page_url}」
- 5, without one (audio, Windows, a video already small): 「剪好了。原来 {原时长}，剪掉 {N} 处空白和气口，现在 {新时长}，文件在这里：{输出文件夹}，原片没动。{这条是音频 / 这台电脑出不了压小的副本 / 原片已经够小}，所以没另出压小的那份。想看具体剪了哪几段：{page_url}」

The cut has places you were unsure of: add 「另外有 {N} 处我拿不准该不该剪，你在意的话点上面的链接听一下，留不留你定。」

## Step 6: one thing by voice, then close

- 1, 2, 3: 「以后不用点按钮，跟我说话就行。现在可以顺手试一句（回数字），或者说没问题了：1. 给我整理一份复习笔记 2. 有句写错了，或者以后都想换个写法，直接告诉我怎么改 3. 发到飞书 4. 学完了（我帮你标上，以后好找没学的） 5. 先这样」
- 4: 「以后也是跟我说话就行。现在可以顺手试一件（回数字），或者收尾：1. 有人名或专业词听错了，告诉我正确写法，我整篇改掉，下次也记得 2. 英文视频的话，把字幕翻成中文 3. 顺便写成一份笔记 4. 先这样」
- 5: 「以后别的录音录像也直接丢给我就行。现在可以顺手试一件（回数字），或者收尾：1. 哪段不该剪、哪里没剪干净，告诉我那句话或时间点，我重新出一版 2. 顺便整理成文字笔记 3. 先这样」

Put each list on its own lines. Do what they pick the way the skill says (review note,
`replace_note_text` / feedback rules, Feishu export, `mark_studied`, cut changes, the
word list, subtitle translation). After one, or 「先这样」:

> 好，第一遍就走完了。以后要处理视频、录音或链接，开个对话直接丢给我就行，FluentFlow 的页面开不开都可以。

## When something goes wrong

- A Douyin image post or a paid video: 「这条是抖音图文（或付费内容），下不成视频。换一条普通的抖音视频链接，或者把电脑里的视频文件发过来。」
- Bilibili or YouTube needs a login: 「这条要登录才能下。最省事是先把视频存到电脑里再发给我。想以后都能直接贴链接的话，打开 FluentFlow 页面的「设置」，在「拿到素材」里选你平时登录它的浏览器，选好告诉我再试。」
- A link that cannot be fetched right now: 「这条刚才没能从平台拿下来。隔两分钟我再试一次，或者你换一条链接。」
- They come back after a long while and ask: 「查了一下，「{标题}」{已经做好了，文件在…… / 已经转完字了，我接着把笔记写完，再等一两分钟}。」

## What it does not do (for option 6)

Checked against the code on 2026-11-27. Answer with the line, then offer the six options.

- Web articles, 公众号 posts, PDFs, e-books: 「文章、公众号、PDF 和电子书它处理不了，它只收音视频（还有字幕文件）。」
- Xiaohongshu, Weibo, Kuaishou and other sites: 「链接目前只认抖音、B 站、YouTube 和视频直链；别的网站的视频，先存到电脑里再发给我。」
- Douyin image posts and paid videos: the line under "When something goes wrong".
- Transitions, music, colour, effects: 「它只剪掉没人说话的空白和气口，不做转场、配乐、调色这类剪辑。」
- Live transcription while someone speaks: 「它不能边说边转，要先录下来，把录音或录像文件给我。」
- On the phone: 「它只在电脑上运行，没有手机版；处理要在这台电脑上做，做好的 PDF 笔记可以发到手机上看。」
- Dubbing into another language: 「它不配音；英文视频可以把字幕翻成中文。」
- Burning subtitles into the picture: 「它不把字幕压进画面，出的是单独的 srt、vtt 字幕文件，播放器里能加载。」
