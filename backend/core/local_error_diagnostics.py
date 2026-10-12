"""User-facing diagnostics for the local edition."""

from __future__ import annotations

import re
from typing import Any


def _diag(
    code: str,
    title: str,
    detail: str,
    next_action: str,
    *,
    retryable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "error",
        "title": title,
        "detail": detail,
        "next_action": next_action,
        "retryable": retryable,
    }


_LINK_SOURCE_FAILURES = (
    ("链接视频过大", "video_link_too_large", "链接视频过大", "先下载到本机，压缩或拆分后再上传。"),
    ("视频时长过长", "media_too_long", "视频时长超过限制", "先下载到本机，拆分后再上传；或在设置里调整时长上限。"),
    ("图文作品没有可转写的音视频", "douyin_image_post", "抖音图文作品", "换一个视频链接。"),
    ("抖音没有给出可下载的视频", "douyin_no_media", "抖音没有可下载的视频", "在抖音里打开确认；能播放的话，下载到本机后上传。"),
    ("只能拿到试看片段", "bilibili_preview_only", "B 站会员内容只有试看片段", "用有权限账号的浏览器登录态，或下载完整视频后上传。"),
    ("这个 B 站链接", "bilibili_link_failed", "B 站链接无法下载", "按提示处理，或上传本地视频。"),
    ("B 站拒绝了", "bilibili_link_failed", "B 站拒绝下载", "稍后重试，或上传本地视频。"),
    ("B 站暂时限制了请求", "platform_rate_limited", "平台请求过于频繁", "过几分钟再试，或上传本地视频。"),
    ("暂时无法解析这个 B 站链接", "bilibili_link_failed", "B 站链接无法解析", "在 B 站里打开确认，或上传本地视频。"),
    ("这个 YouTube 链接", "youtube_link_failed", "YouTube 视频无法获取", "按提示处理，或上传视频文件。"),
    ("YouTube 要求验证", "youtube_bot_check", "YouTube 机器人验证", "在设置里选择已登录 YouTube 的浏览器，或上传视频文件。"),
    ("登录：系统不允许 FluentFlow 读取这个浏览器的数据", "browser_login_unreadable", "读不到浏览器登录", "允许完全磁盘访问权限，或下载到本机后上传。"),
    ("抖音视频解析成功，但下载没有成功", "douyin_download_failed", "抖音视频下载失败", "稍后再提交一次，或下载到本机后上传。"),
)


# ---- Feishu export ----------------------------------------------------------
#
# Route names match Settings → 导出: 「飞书导出路线」 with 「本机身份导出」
# (lark-cli) and 「飞书应用导出」 (App ID / App Secret).

_FEISHU_TEXT = re.compile(r"lark-cli|feishu|飞书|\blark\b", re.IGNORECASE)
_PARTIAL_DOC = re.compile(r"已建好的半截文档：(\S+)")
_CLI_MESSAGE = re.compile(r"lark-cli 失败 \[[^\]]*\]：(.*?)(?:（hint:|$)", re.DOTALL)
_SCOPE_LIST = re.compile(r"scopes?\s+(?:is\s+|are\s+)?required[^\[]*\[([^\]]+)\]", re.IGNORECASE)
_SCOPE_NAME = re.compile(r"\b[a-z_]+(?::[a-z_.]+)+\b")
_APP_CREDENTIAL_CODE = re.compile(r"code['\"]?\s*[=:]\s*(10003|10014|99991663|99991664)\b")
_CJK = re.compile(r"[一-鿿]")


def _missing_scopes(raw: str) -> list[str]:
    tagged = re.search(r"missing_scopes=([^\s\]]+)", raw)
    if tagged:
        return [s for s in tagged.group(1).split(",") if s]
    listed = _SCOPE_LIST.search(raw)
    if listed:
        return [s.strip(" '\"") for s in listed.group(1).split(",") if s.strip(" '\"")]
    return [s for s in _SCOPE_NAME.findall(raw) if s.split(":")[0] in {"wiki", "docx", "docs", "drive", "space"}]


def _feishu_diagnosis(raw: str, lowered: str) -> dict[str, Any] | None:
    if not _FEISHU_TEXT.search(raw):
        return None
    via_cli = "lark-cli" in lowered
    cli_message = _CLI_MESSAGE.search(raw)
    cli_words = cli_message.group(1).strip() if cli_message else ""
    diagnosis = _feishu_rule(raw, lowered, via_cli)
    # lark-cli's own Chinese sentence is closer to the cause than ours; where
    # ours names something it may not (a scope, the login), keep both.
    if cli_words and _CJK.search(cli_words) and cli_words not in diagnosis["detail"]:
        if diagnosis["code"] in {"feishu_missing_scope", "lark_cli_login_required"}:
            diagnosis["detail"] = f"{diagnosis['detail']} lark-cli 的原话：{cli_words}"
        elif diagnosis["code"] != "lark_cli_not_installed":
            diagnosis["detail"] = cli_words
    partial = _PARTIAL_DOC.search(raw)
    if partial:
        diagnosis["detail"] = (
            f"{diagnosis['detail']} 文档已经建好但只写了一部分：{partial.group(1)}，可以打开看看或删掉。"
        )
    return diagnosis


def _feishu_rule(raw: str, lowered: str, via_cli: bool) -> dict[str, Any]:
    if "lark-cli not found" in lowered:
        return _diag(
            "lark_cli_not_installed",
            "没有找到 lark-cli",
            "「本机身份导出」要用飞书命令行工具 lark-cli，这台电脑上没有找到它。",
            "在终端运行 npm install -g @larksuite/cli 安装，再运行 lark-cli auth login 登录；"
            "或在设置的「飞书导出路线」里改选「飞书应用导出」。",
            retryable=False,
        )
    if (
        "missing_scope" in lowered
        or "99991679" in lowered
        or "99991672" in lowered
        or _SCOPE_LIST.search(raw)
        or "createwikinodeinspace" in lowered
    ):
        scopes = _missing_scopes(raw)
        named = "、".join(scopes)
        if via_cli:
            return _diag(
                "feishu_missing_scope",
                "飞书登录缺少权限",
                f"当前 lark-cli 登录身份缺少导出需要的权限{f'（{named}）' if named else ''}。",
                f'在终端运行 lark-cli auth login --scope "{" ".join(scopes)}" 补授权后重试。'
                if scopes else "在终端运行 lark-cli auth login --domain docs --domain drive 补授权后重试。",
                retryable=False,
            )
        return _diag(
            "feishu_missing_scope",
            "飞书应用缺少权限",
            f"你的飞书应用没有开通导出需要的权限{f'（{named}）' if named else ''}。",
            f"到飞书开放平台 → 你的应用 → 权限管理，开通{f' {named} ' if named else '云文档相关权限'}，"
            "发布新版本后重试；或在设置的「飞书导出路线」里改选「本机身份导出」。",
            retryable=False,
        )
    if via_cli and any(
        token in lowered
        for token in (
            "not logged in", "not login", "auth login", "token expired", "token_expired",
            "need_user_authorization", "type=authentication", "unauthorized", "refresh token",
            "99991668", "99991677", "未登录", "登录已过期",
        )
    ):
        return _diag(
            "lark_cli_login_required",
            "本机飞书登录失效",
            "这台电脑上的 lark-cli 没有可用的登录身份（没登录或登录已过期）。",
            "在终端运行 lark-cli auth login 重新登录，再回来导出。",
        )
    if "lark credentials not set" in lowered:
        return _diag(
            "feishu_app_credentials_missing",
            "没有填飞书应用凭证",
            "「飞书应用导出」需要 App ID 和 App Secret，现在还没有填。",
            "到设置「高级 · 其他凭证」里填写 FEISHU APP ID 和 FEISHU APP SECRET；"
            "或在「飞书导出路线」里改选「本机身份导出」。",
            retryable=False,
        )
    if (
        "tenant token error" in lowered
        or "app secret invalid" in lowered
        or "invalid app_secret" in lowered
        or "app_secret invalid" in lowered
        or _APP_CREDENTIAL_CODE.search(lowered)
    ):
        return _diag(
            "feishu_app_credentials_invalid",
            "飞书应用凭证不对",
            "飞书不接受当前的 App ID / App Secret（填错了，或 Secret 已经重置）。",
            "到飞书开放平台 → 你的应用 → 凭证与基础信息，复制最新的 App ID 和 App Secret，"
            "填回设置「高级 · 其他凭证」后重试。",
            retryable=False,
        )
    if "folder not found" in lowered or "http 404" in lowered or "notexist" in lowered or "文件夹不存在" in raw:
        return _diag(
            "feishu_folder_not_found",
            "飞书目标文件夹不存在",
            "飞书没有找到设置里填的目标文件夹，可能已被删除或移动。",
            "到设置「导出」里重新填写飞书文件夹链接，或清空它后重试。",
            retryable=False,
        )
    if any(
        token in lowered
        for token in ("99991400", "frequency limit", "too many requests", "http 429", "rate limit", "ratelimit")
    ) or "请求过于频繁" in raw or "限流" in raw:
        return _diag(
            "feishu_rate_limited",
            "飞书暂时限制了请求",
            "飞书这段时间收到的请求太多，暂时拒绝了这次导出。",
            "过一两分钟再导出一次。",
        )
    if "图片上传失败" in raw:
        return _diag(
            "feishu_image_upload_failed",
            "飞书图片上传失败",
            "飞书导出时图片上传失败，文本笔记可能仍可用。",
            "检查飞书应用的图片上传权限后重试。",
        )
    if "permission denied" in lowered or "forbidden" in lowered or "http 403" in lowered or "no permission" in lowered:
        return _diag(
            "feishu_permission_denied",
            "飞书拒绝了写入",
            "飞书拒绝了这次写入：当前身份对目标位置没有编辑权限。",
            "如果设置里填了飞书文件夹，先把它共享给你的应用（可编辑）或清空它；"
            "也可以在「飞书导出路线」里改选「本机身份导出」。",
            retryable=False,
        )
    short = raw if len(raw) <= 240 else raw[:240] + "…"
    if via_cli:
        return _diag(
            "lark_cli_export_failed",
            "lark-cli 导出失败",
            f"lark-cli 导出失败：{short}",
            "在终端运行 lark-cli auth status 看登录状态，处理后重试。",
        )
    return _diag(
        "feishu_export_failed",
        "飞书导出失败",
        f"飞书导出失败：{short}",
        "检查设置里的「飞书导出路线」和对应的登录或应用凭证后重试。",
    )


def diagnose_error(error: Any) -> dict[str, Any]:
    raw = str(error or "").strip()
    if not raw:
        return _diag(
            "unknown_error",
            "任务处理失败",
            "处理失败，但没有返回具体原因。请重试一次。",
            "重新提交任务；如果连续失败，请查看本机日志。",
        )
    lowered = raw.lower()

    feishu = _feishu_diagnosis(raw, lowered)
    if feishu is not None:
        return feishu

    if "no module named yt_dlp" in lowered or "no module named 'yt_dlp'" in lowered:
        return _diag(
            "video_parser_config_missing",
            "视频解析组件缺失",
            "本机缺少视频链接解析组件。",
            "安装项目依赖后重启 FluentFlow，或暂时上传本地视频。",
            retryable=False,
        )
    if "目前只支持抖音、bilibili、youtube 或视频直链" in lowered:
        return _diag(
            "unsupported_video_source",
            "暂不支持这个视频来源",
            "当前链接不属于 FluentFlow 已支持的视频来源。",
            "改用支持的视频链接，或上传本地视频。",
            retryable=False,
        )
    # Link failures whose own sentence already says what happened and what to
    # do. Retrying the same link gives the same answer, so none of them may
    # suggest it.
    for token, code, title, action in _LINK_SOURCE_FAILURES:
        if token in raw:
            return _diag(code, title, raw, action, retryable=False)
    if "fresh cookies" in lowered or "cookies are needed" in lowered:
        return _diag(
            "douyin_fresh_cookies",
            "抖音拒绝了不带登录的请求",
            "抖音要求先在浏览器里访问过抖音（需要新的 cookies），这次请求被拒绝了。",
            "在浏览器里打开 douyin.com（登录更好），在设置里选这个浏览器的登录态后再提交；"
            "或把视频下载到本机后上传。",
            retryable=False,
        )
    if any(
        token in raw
        for token in ("视频链接只允许使用公网", "视频下载地址不属于允许的公网媒体域名", "视频链接端口无效", "无账号信息的公网地址")
    ):
        return _diag(
            "video_source_address_refused",
            "视频地址不在允许范围",
            "这个视频的下载地址用了 FluentFlow 不访问的端口或域名，重试也是同样结果。",
            "把视频下载到本机后上传。",
            retryable=False,
        )
    if "必须解析到公网地址" in raw or "不能访问本机、内网或云元数据服务" in raw:
        return _diag(
            "unsafe_video_source_url",
            "视频链接无法安全访问",
            "这个地址指向不可安全请求的位置。",
            "改用可公开访问的视频链接，或直接上传本地文件。",
            retryable=False,
        )
    if any(
        token in lowered
        for token in (
            "incorrect api key",
            "invalid_api_key",
            "apikey-error",
            "api-key-error",
            # DeepSeek: "Authentication Fails, Your api key: ****abcd is invalid"
            "authentication fails",
            "authentication_error",
        )
    ):
        return _diag(
            "invalid_api_key",
            "AI API Key 无效",
            "AI 笔记没有生成：当前 API Key 无效或已失效，转录和字幕仍会保留。",
            "到设置页更新自己的 AI API Key，然后重生笔记。",
        )
    if "no position encodings are defined" in lowered:
        return _diag(
            "local_diarization_too_long",
            "本地说话人区分失败",
            "本地说话人区分模型无法处理当前音频长度。",
            "关闭说话人区分后重新处理。",
        )

    if any(
        token in lowered
        for token in ("po token", "gvs po token", "sabr streaming", "the page needs to be reloaded")
    ) or "youtube 原视频下载受限" in raw:
        return _diag(
            "youtube_media_restricted",
            "YouTube 原视频下载受限",
            "YouTube 字幕不可用，同时原视频下载被客户端校验拦住。",
            "上传本地视频或字幕文件，或配置高级本地下载环境后重试。",
        )
    if any(token in lowered for token in ("no captions", "no subtitles")) or "没有可用字幕" in raw:
        return _diag(
            "youtube_no_captions",
            "YouTube 没有可用字幕",
            "这个视频没有获取到可用字幕。",
            "上传本地视频、音频或字幕文件。",
        )
    if (
        "http error 403" in lowered
        or "视频下载失败：403" in raw
        or "forbidden" in lowered
        or "平台拒绝了这个链接" in raw
    ):
        return _diag(
            "platform_forbidden",
            "平台拒绝下载",
            "视频平台拒绝了这个链接的下载请求。",
            "稍后重试、在设置里开启浏览器登录态（cookies），或上传本地视频。",
        )
    if any(
        token in lowered
        for token in ("error code: 429", "rate limit reached", "rate_limit_error", "rate limit exceeded")
    ):
        return _diag(
            "ai_rate_limited",
            "AI 服务限流",
            "AI 笔记没有生成：AI 服务说请求过于频繁或额度用完，暂时被限流，转录和字幕仍会保留。",
            "稍后重生笔记；如果反复出现，到服务商后台查看套餐额度和并发限制。",
        )
    if "http error 429" in lowered or "too many requests" in lowered:
        return _diag(
            "platform_rate_limited",
            "平台请求过于频繁",
            "视频平台暂时限制了请求。",
            "稍后重试，或直接上传本地视频/字幕文件。",
        )
    # Before the download-timeout rule, which matches any "timeout": on the
    # local edition a long recording on a slow machine hits the transcription
    # time limit, and blaming the download would send the reader nowhere.
    if "stt processing timed out" in lowered or "转写超时" in raw:
        return _diag(
            "stt_timeout",
            "转写超时",
            "转写超过时间上限被中止，通常是音视频太长或这台机器跑不完。",
            "把素材拆短再试，或在设置里换更快的转写档位。",
        )
    if "视频下载超时" in raw or "timed out" in lowered or "timeout" in lowered:
        return _diag(
            "video_download_timeout",
            "视频下载超时",
            "视频下载时间过长，可能是文件较大或当前网络较慢。",
            "稍后重试，或上传本地视频。",
        )
    if "抖音的登录信息过期了" in raw:
        return _diag(
            "douyin_login_expired",
            "抖音登录信息过期",
            raw,
            "在浏览器里打开 douyin.com 登录一次，再重试这个链接。",
        )
    if "暂时无法自动解析这个视频链接" in raw:
        return _diag(
            "video_link_parse_failed",
            "链接暂时无法解析",
            "暂时无法自动解析这个视频链接。",
            "换一个分享链接，或直接上传视频文件。",
        )
    if "没有识别到视频链接" in raw:
        return _diag(
            "video_link_missing",
            "没有识别到视频链接",
            "没有识别到完整的视频 URL。",
            "粘贴完整分享文本或视频 URL 后重试。",
        )

    if "downloaded video is too large" in lowered or "file is too large" in lowered or "视频文件过大" in raw:
        return _diag(
            "file_too_large",
            "文件超过限制",
            "文件超过当前处理限制。",
            "压缩或拆分文件后重试，或调整本机限制。",
            retryable=False,
        )
    if "一次最多提交" in raw or "too many files uploaded" in lowered:
        return _diag(
            "too_many_files",
            "一次提交的文件太多",
            raw if "一次最多提交" in raw else "一次提交的文件数超过了本机限制。",
            "分成几批提交，每批不超过限制数量。",
            retryable=False,
        )
    if "unsupported transcript file type" in lowered:
        return _diag(
            "unsupported_transcript_type",
            "字幕格式不支持",
            "不支持这个字幕或转录文件格式。",
            "换成 SRT、VTT、TXT 或 Markdown 后重试。",
            retryable=False,
        )
    if "unsupported file type" in lowered:
        return _diag(
            "unsupported_file_type",
            "文件格式不支持",
            "不支持这个媒体文件格式。",
            "换成支持的视频或音频文件后重试。",
            retryable=False,
        )
    if "no file uploaded" in lowered:
        return _diag(
            "file_missing",
            "没有收到文件",
            "没有收到上传文件。",
            "重新选择文件后提交。",
        )
    media_failures = (
        ("媒体文件为空", "media_file_empty", "媒体文件为空", "重新选择原始媒体文件后提交。"),
        ("媒体内容与文件扩展名不一致", "media_extension_mismatch", "媒体格式与扩展名不一致", "使用原始文件或正确的扩展名后重新提交。"),
        ("没有可转录的音轨", "media_audio_stream_missing", "没有可转录的音轨", "上传包含系统声音或麦克风声音的音视频文件。"),
    )
    for token, code, title, action in media_failures:
        if token in raw:
            return _diag(code, title, raw, action, retryable=False)
    if "媒体文件无法读取" in raw or "媒体中的音频无法读取" in raw:
        return _diag(
            "media_unreadable",
            "媒体文件无法读取",
            "文件可能已损坏，或内容与扩展名不匹配。",
            "重新导出或更换媒体文件后提交。",
            retryable=False,
        )
    if "媒体预检暂不可用" in raw:
        return _diag(
            "media_preflight_unavailable",
            "媒体预检暂不可用",
            "本机暂时无法安全检查媒体文件。",
            "检查 FFmpeg 环境后重试。",
        )
    if (
        "queued source file is missing" in lowered
        or "原始文件已不存在" in raw
        or "已按保留策略清理" in raw
        or "source file not found" in lowered
    ):
        return _diag(
            "source_file_missing",
            "原始文件已不存在",
            raw if "已按保留策略清理" in raw else "后台任务找不到原始文件，文件可能已被清理。",
            "重新上传原始文件后再处理。",
            retryable=False,
        )
    if "queued processing request failed" in lowered:
        return _diag(
            "queue_processing_failed",
            "后台队列调用失败",
            "本机后台任务调用转录接口失败。",
            "重试；如果连续出现，重启 FluentFlow 后再提交。",
        )
    if "queued transcript summary request failed" in lowered:
        return _diag(
            "queue_summary_failed",
            "后台笔记生成调用失败",
            "本机后台任务调用笔记生成接口失败。",
            "重试；如果转录已保存，打开结果后重生笔记。",
        )
    if "job not found" in lowered or "404" in lowered or "归属" in raw:
        return _diag(
            "job_not_found",
            "没有找到任务",
            "任务记录没有在当前本机任务库中找到。",
            "刷新处理记录；如果仍找不到，请重新提交任务。",
        )
    if "unsupported note generation mode" in lowered or "chapter_coverage" in lowered:
        return _diag(
            "unsupported_note_mode",
            "笔记模式不受支持",
            "当前版本不支持这类笔记生成模式。",
            "选择“自动”或“高保真”后重新提交。",
            retryable=False,
        )
    if "empty result" in lowered or "returned empty" in lowered or "空笔记" in raw:
        return _diag(
            "empty_ai_note",
            "AI 返回了空笔记",
            "AI 没有生成可用内容。",
            "重生笔记；如果重复出现，调整提示词或更换模型。",
        )
    if "失去了访问自己程序文件夹的权限" in raw or (
        "operation not permitted" in lowered and "/" not in raw
    ):
        return _diag(
            "backend_folder_access_lost",
            "后台服务没有文件夹权限",
            "后台服务失去了访问自己程序文件夹的权限（macOS 隐私保护），重试不会好。",
            "退出 FluentFlow 再重新打开；还不行就到「系统设置 → 隐私与安全性 → 文件与文件夹」，给启动它的终端或应用打开「文稿」权限。",
            retryable=False,
        )
    if "视频下载失败" in raw:
        return _diag(
            "video_download_failed",
            "视频下载失败",
            raw,
            "稍后重试、配置浏览器 cookies，或上传本地视频。",
        )
    return _diag(
        "unknown_error",
        "任务处理失败",
        raw,
        "重试一次；如果连续失败，请查看本机日志。",
    )
