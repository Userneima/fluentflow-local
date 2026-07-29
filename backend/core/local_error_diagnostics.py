"""User-facing diagnostics for the local edition."""

from __future__ import annotations

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
        for token in ("incorrect api key", "invalid_api_key", "apikey-error", "api-key-error")
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
    if "http error 403" in lowered or "视频下载失败：403" in raw or "forbidden" in lowered:
        return _diag(
            "platform_forbidden",
            "平台拒绝下载",
            "平台拒绝了当前视频下载请求。",
            "稍后重试、配置浏览器 cookies，或上传本地视频。",
        )
    if "http error 429" in lowered or "too many requests" in lowered:
        return _diag(
            "platform_rate_limited",
            "平台请求过于频繁",
            "视频平台暂时限制了请求。",
            "稍后重试，或直接上传本地视频/字幕文件。",
        )
    if "视频下载超时" in raw or "timed out" in lowered or "timeout" in lowered:
        return _diag(
            "video_download_timeout",
            "视频下载超时",
            "视频下载时间过长，可能是文件较大或当前网络较慢。",
            "稍后重试，或上传本地视频。",
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
    if "queued source file is missing" in lowered or "原始文件已不存在" in raw:
        return _diag(
            "source_file_missing",
            "原始文件已不存在",
            "后台任务找不到原始文件，文件可能已被清理。",
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
    if "lark-cli" in lowered and any(
        token in lowered for token in ("login", "not logged", "unauthorized", "auth")
    ):
        return _diag(
            "lark_cli_login_required",
            "本机飞书登录失效",
            "当前 lark-cli 没有可用登录身份。",
            "在本机重新登录 lark-cli 后重试导出。",
        )
    if "图片上传失败" in raw:
        return _diag(
            "feishu_image_upload_failed",
            "飞书图片上传失败",
            "飞书导出时图片上传失败，文本笔记可能仍可用。",
            "检查飞书应用的图片上传权限后重试。",
        )
    if "feishu" in lowered or "飞书" in raw or "lark" in lowered:
        return _diag(
            "feishu_export_failed",
            "飞书导出失败",
            "飞书导出失败。",
            "检查本机登录、应用凭据和目标文档权限后重试。",
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
