"""Resolve and download shared video links for FluentFlow."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.title_display import display_title_for_user

SOURCE_INFO_FILE = "视频链接相关信息.md"
DEFAULT_MAX_VIDEO_BYTES = 600 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s，。！？、'\"“”‘’）)\]】]+", re.I)
MIUISTORE_ORIGIN = "https://sph.miuistore.com"
MIUISTORE_HOST_SUFFIXES = ("miuistore.com",)
DOUYIN_MEDIA_HOST_SUFFIXES = ("douyinvod.com", "amemv.com", "snssdk.com")
BILIBILI_MEDIA_HOST_SUFFIXES = ("bilivideo.com", "hdslb.com", "akamaized.net")
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


@dataclass
class VideoSourceProgress:
    stage: str
    message: str
    percent: int | None = None
    loaded_bytes: int | None = None
    total_bytes: int | None = None


@dataclass
class ResolvedVideo:
    provider: str
    source_url: str
    download_url: str
    video_id: str | None = None
    title: str | None = None
    thumbnail_url: str | None = None
    audio_url: str | None = None
    referer: str | None = None
    duration_seconds: float | None = None
    estimated_size_bytes: int | None = None
    resolution_trace: list[dict[str, str]] | None = None


@dataclass
class SavedVideoSource:
    ok: bool
    provider: str
    source_url: str
    download_url: str
    video_id: str
    raw_title: str
    display_title: str
    title: str
    filename: str
    file_path: str
    file_url: str
    metadata_path: str
    size_bytes: int
    downloaded_at: str
    media_type: str = "video"
    asset_strategy: dict[str, Any] | None = None
    duration_seconds: float | None = None
    estimated_size_bytes: int | None = None
    audio_url: str | None = None
    referer: str | None = None
    resolution_trace: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ProgressCallback = Callable[[VideoSourceProgress], None]


class VideoSourceResolutionError(RuntimeError):
    """A resolver failure with a safe, provider-level diagnostic trail."""

    def __init__(self, message: str, resolution_trace: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.resolution_trace = resolution_trace


class VideoSourceCancelled(RuntimeError):
    """Raised when a local caller cancels blocking source work."""


def _raise_if_cancelled(cancellation_event: threading.Event | None) -> None:
    if cancellation_event is not None and cancellation_event.is_set():
        raise VideoSourceCancelled("Video source operation cancelled")


def _cancellation_kwargs(
    cancellation_event: threading.Event | None,
) -> dict[str, threading.Event]:
    return {"cancellation_event": cancellation_event} if cancellation_event is not None else {}


def _run_process(
    args: list[str],
    *,
    timeout: float,
    cancellation_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a child process, terminating it promptly when locally cancelled.

    Callers that do not provide a cancellation event retain the existing
    ``subprocess.run`` path. The polling ``Popen`` path is local-only and keeps
    yt-dlp/FFmpeg from continuing after a user cancels the owning task.
    """
    if cancellation_event is None:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

    _raise_if_cancelled(cancellation_event)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout
    while True:
        if cancellation_event.is_set():
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise VideoSourceCancelled("Video source operation cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        try:
            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def max_video_bytes() -> int:
    try:
        parsed = int(os.environ.get("VIDEO_SOURCE_MAX_BYTES", ""))
        return parsed if parsed > 0 else DEFAULT_MAX_VIDEO_BYTES
    except ValueError:
        return DEFAULT_MAX_VIDEO_BYTES


def trim_url(value: str) -> str:
    return value.strip().rstrip(")）]】\"'“”‘’。，,")


def extract_first_url(input_text: str) -> str | None:
    match = URL_RE.search(input_text or "")
    return trim_url(match.group(0)) if match else None


def parse_http_url(value: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http/https 视频链接")
    return parsed


def _host_matches_suffixes(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes)


def trusted_media_host_suffixes(value: str) -> tuple[str, ...]:
    host = (urllib.parse.urlparse(value).hostname or "").lower().rstrip(".")
    if _host_matches_suffixes(host, DOUYIN_MEDIA_HOST_SUFFIXES):
        return DOUYIN_MEDIA_HOST_SUFFIXES
    if _host_matches_suffixes(host, BILIBILI_MEDIA_HOST_SUFFIXES):
        return BILIBILI_MEDIA_HOST_SUFFIXES
    return ()


def validate_public_http_url(
    value: str,
    *,
    allowed_host_suffixes: tuple[str, ...] = (),
) -> urllib.parse.ParseResult:
    parsed = parse_http_url(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or parsed.username or parsed.password:
        raise ValueError("视频链接必须是无账号信息的公网地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("视频链接端口无效") from exc
    if port not in {None, 80, 443}:
        raise ValueError("视频链接只允许使用公网 HTTP/HTTPS 端口")
    if allowed_host_suffixes and not _host_matches_suffixes(host, allowed_host_suffixes):
        raise ValueError("视频下载地址不属于允许的公网媒体域名")

    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError("视频链接无法解析为可访问的公网地址") from exc
    proxy_fake_ip_allowed = bool(allowed_host_suffixes)
    if not addresses or any(
        (not address.is_global or address.is_multicast)
        and not (proxy_fake_ip_allowed and address in PROXY_FAKE_IP_NETWORK)
        for address in addresses
    ):
        raise ValueError("视频链接必须解析到公网地址，不能访问本机、内网或云元数据服务")
    return parsed


class PublicHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the public network or an optional host allowlist."""

    def __init__(self, allowed_host_suffixes: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.allowed_host_suffixes = allowed_host_suffixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl, allowed_host_suffixes=self.allowed_host_suffixes)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_http_url(
    request_or_url: urllib.request.Request | str,
    *,
    timeout: float,
    allowed_host_suffixes: tuple[str, ...] = (),
):
    url = request_or_url.full_url if isinstance(request_or_url, urllib.request.Request) else request_or_url
    validate_public_http_url(url, allowed_host_suffixes=allowed_host_suffixes)
    opener = urllib.request.build_opener(PublicHTTPRedirectHandler(allowed_host_suffixes))
    return opener.open(request_or_url, timeout=timeout)


def sanitize_filename_part(value: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|#%&{}$!'@+`=]", " ", value or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:72]


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def video_id_from_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        video_id = (query.get("video_id") or [None])[0]
        if video_id:
            return video_id
        tokens = [token for token in parsed.path.split("/") if token]
        if tokens and re.match(r"^[a-zA-Z0-9_-]{6,}$", tokens[-1]):
            return tokens[-1][:40]
    except Exception:
        pass
    return stable_id(url)


def display_title_for_source_input(input_text: str, fallback: str = "") -> str:
    source_url = extract_first_url(input_text or "")
    if not source_url:
        return display_title_for_user(fallback or input_text, fallback or input_text)
    try:
        parsed = urllib.parse.urlparse(source_url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        video_id = video_id_from_url(source_url)
        if "bilibili.com" in host or host == "b23.tv":
            return f"Bilibili 视频 {video_id}" if video_id else "Bilibili 视频"
        if "douyin.com" in host:
            return "抖音视频链接"
        if "youtube.com" in host or host == "youtu.be":
            return "YouTube 视频"
        if host:
            return f"{host} 视频链接"
    except Exception:
        pass
    return display_title_for_user(fallback or input_text, fallback or input_text) or "视频链接"


def resolve_filename(video: ResolvedVideo, requested_title: str | None = None, extension: str = ".mp4") -> tuple[str, str, str, str]:
    video_id = sanitize_filename_part(video.video_id or video_id_from_url(video.source_url)) or stable_id(video.source_url)
    raw_title = sanitize_filename_part(requested_title or video.title or f"视频-{video_id}") or f"视频-{video_id}"
    display_title = display_title_for_user(raw_title, raw_title) or raw_title
    filename_title = sanitize_filename_part(display_title) or raw_title
    safe_extension = extension if extension.startswith(".") else f".{extension}"
    return video_id, raw_title, display_title, f"{video_id}-{filename_title}{safe_extension}"


def decode_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def strip_tags(value: str) -> str:
    return decode_html(re.sub(r"<[^>]*>", " ", value or ""))


def parse_miuistore_field(page_html: str, label: str) -> str | None:
    pattern = re.compile(
        rf"{re.escape(label)}：\s*</div>\s*<div[^>]*class=['\"][^'\"]*\bcol-value\b[^'\"]*['\"][^>]*>([\s\S]*?)</div>",
        re.I,
    )
    match = pattern.search(page_html or "")
    return strip_tags(match.group(1)) if match else None


def parse_miuistore_links(page_html: str) -> list[str]:
    links = []
    for match in re.finditer(r'href="([^"]+)"', page_html or "", re.I):
        href = decode_html(match.group(1))
        if href.startswith(("http://", "https://")):
            links.append(href)
    return links


def choose_miuistore_video_url(links: list[str]) -> str | None:
    for href in links:
        try:
            parsed = parse_http_url(href)
            host = (parsed.hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if _host_matches_suffixes(host, DOUYIN_MEDIA_HOST_SUFFIXES) and (
            "mime_type=video_mp4" in href or "douyinvod.com" in host or "/aweme/v1/play/" in parsed.path
        ):
            return href
    return None


def is_bilibili_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        return host.endswith("bilibili.com") or host == "b23.tv"
    except Exception:
        return False


def is_douyin_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        return host == "douyin.com" or host.endswith(".douyin.com")
    except Exception:
        return False


def is_youtube_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        return host == "youtu.be" or host.endswith("youtube.com")
    except Exception:
        return False


def fetch_text(
    url: str,
    timeout: float = 45,
    *,
    allowed_host_suffixes: tuple[str, ...] = (),
) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "user-agent": "Mozilla/5.0 FluentFlow/1.0",
            "accept": "text/html,application/json,*/*",
        },
    )
    with open_public_http_url(
        request,
        timeout=timeout,
        allowed_host_suffixes=allowed_host_suffixes,
    ) as response:
        return response.read().decode("utf-8", errors="replace")


def is_probably_direct_video_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        return (
            parsed.path.lower().endswith(".mp4")
            or (query.get("mime_type") or [None])[0] == "video_mp4"
            or "douyinvod.com" in (parsed.hostname or "")
        )
    except Exception:
        return False


def resolve_direct_video(url: str) -> ResolvedVideo | None:
    if not is_probably_direct_video_url(url):
        return None
    return ResolvedVideo(
        provider="direct",
        source_url=url,
        download_url=url,
        video_id=video_id_from_url(url),
    )


def run_yt_dlp(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
) -> dict[str, Any]:
    args = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--no-playlist", url]
    cookies = (cookies_from_browser or os.environ.get("YT_DLP_COOKIES_FROM_BROWSER", "")).strip()
    if cookies:
        args.insert(3, "--cookies-from-browser")
        args.insert(4, cookies)
    try:
        if is_youtube_url(url):
            url_arg = args.pop()
            args.extend(["--extractor-args", "youtube:player_client=android", url_arg])
    except Exception:
        pass
    if is_bilibili_url(url):
        url_arg = args.pop()
        args.extend([
            "--add-header",
            "Referer:https://www.bilibili.com/",
            "--add-header",
            "Origin:https://www.bilibili.com",
            "--add-header",
            f"User-Agent:{BILIBILI_USER_AGENT}",
            url_arg,
        ])
    result = _run_process(args, timeout=90, cancellation_event=cancellation_event)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "").strip() or f"yt-dlp 退出码 {result.returncode}")
    return json.loads(result.stdout)


def choose_yt_dlp_media(info: dict[str, Any]) -> tuple[str | None, str | None]:
    direct_url = info.get("url")
    if direct_url and (info.get("ext") == "mp4" or "mime_type=video_mp4" in direct_url or ".mp4" in direct_url):
        return str(direct_url), None

    combined: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    audios: list[dict[str, Any]] = []
    for item in info.get("formats") or []:
        if not item.get("url"):
            continue
        has_video = item.get("vcodec") not in {None, "none"}
        has_audio = item.get("acodec") not in {None, "none"}
        if has_video and has_audio:
            combined.append(item)
        elif has_video:
            videos.append(item)
        elif has_audio:
            audios.append(item)

    def video_score(item: dict[str, Any]) -> tuple[int, int, int]:
        return (
            1 if item.get("ext") == "mp4" else 0,
            int(item.get("height") or 0),
            int(item.get("filesize") or item.get("filesize_approx") or 0),
        )

    def audio_score(item: dict[str, Any]) -> tuple[int, int]:
        return (
            int(item.get("abr") or item.get("tbr") or 0),
            int(item.get("filesize") or item.get("filesize_approx") or 0),
        )

    if combined:
        combined.sort(key=video_score, reverse=True)
        return str(combined[0]["url"]), None
    if videos and audios:
        videos.sort(key=video_score, reverse=True)
        audios.sort(key=audio_score, reverse=True)
        return str(videos[0]["url"]), str(audios[0]["url"])
    if audios:
        audios.sort(key=audio_score, reverse=True)
        return str(audios[0]["url"]), None
    if videos:
        videos.sort(key=video_score, reverse=True)
        return str(videos[0]["url"]), None
    return None, None


def _number_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def estimate_yt_dlp_size_bytes(info: dict[str, Any]) -> int | None:
    direct = _number_or_none(info.get("filesize") or info.get("filesize_approx"))
    if direct:
        return int(direct)
    sizes = []
    for item in info.get("formats") or []:
        size = _number_or_none(item.get("filesize") or item.get("filesize_approx"))
        if size:
            sizes.append(int(size))
    return max(sizes) if sizes else None


def _resolver_failure_reason(error: Exception) -> str:
    reason = video_source_failure_reason(error)
    return reason if reason != "unknown" else "unavailable"


# Douyin answers "fresh cookies are needed" to a share link intermittently, with
# no pattern and no change on our side: measured 2026-09-03 on one link, the same
# call alternated between a full JSON answer and that refusal within seconds,
# with and without browser cookies. One attempt therefore decides a link's fate
# on a coin flip, which is how a link that works by hand comes back
# "暂时无法自动解析" in the product. Retried only for that reason — a genuinely
# unsupported or private video fails the same way every time and must not be
# hammered.
_TRANSIENT_RESOLVER_REASONS = frozenset({"fresh_cookies_required"})
_RESOLVER_RETRIES = 3
# Escalating, because the refusal looks like throttling: measured on one link,
# two attempts 2s apart still lost one run in three, while backing off further
# recovered it.
_RESOLVER_RETRY_SLEEP_SECONDS = 2.0


def _resolve_with_yt_dlp_attempt(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
) -> tuple[ResolvedVideo | None, str | None]:
    resolved, reason = _resolve_with_yt_dlp_once(
        url, cookies_from_browser, cancellation_event=cancellation_event
    )
    attempts = 0
    while resolved is None and reason in _TRANSIENT_RESOLVER_REASONS and attempts < _RESOLVER_RETRIES:
        attempts += 1
        _raise_if_cancelled(cancellation_event)
        pause = _RESOLVER_RETRY_SLEEP_SECONDS * attempts
        if cancellation_event is not None:
            if cancellation_event.wait(pause):
                _raise_if_cancelled(cancellation_event)
        else:
            time.sleep(pause)
        resolved, reason = _resolve_with_yt_dlp_once(
            url, cookies_from_browser, cancellation_event=cancellation_event
        )
    return resolved, reason


def _resolve_with_yt_dlp_once(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
) -> tuple[ResolvedVideo | None, str | None]:
    try:
        info = run_yt_dlp(url, cookies_from_browser, **_cancellation_kwargs(cancellation_event))
        download_url, audio_url = choose_yt_dlp_media(info)
        if not download_url:
            return None, "no_downloadable_media"
        return ResolvedVideo(
            provider="yt-dlp",
            source_url=info.get("webpage_url") or info.get("original_url") or url,
            download_url=download_url,
            video_id=info.get("id"),
            title=info.get("title"),
            thumbnail_url=info.get("thumbnail"),
            audio_url=audio_url,
            referer="https://www.bilibili.com/" if is_bilibili_url(info.get("webpage_url") or url) else None,
            duration_seconds=_number_or_none(info.get("duration")),
            estimated_size_bytes=estimate_yt_dlp_size_bytes(info),
        ), None
    except VideoSourceCancelled:
        raise
    except Exception as exc:
        return None, _resolver_failure_reason(exc)


def resolve_with_yt_dlp(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
) -> ResolvedVideo | None:
    resolved, _ = _resolve_with_yt_dlp_attempt(
        url, cookies_from_browser, **_cancellation_kwargs(cancellation_event)
    )
    return resolved


def _resolve_with_miuistore_attempt(
    input_text: str,
    *,
    cancellation_event: threading.Event | None = None,
) -> tuple[ResolvedVideo | None, str | None]:
    try:
        _raise_if_cancelled(cancellation_event)
        source_url = extract_first_url(input_text)
        if not source_url or not is_douyin_url(source_url):
            return None, "unsupported_source"
        check_url = f"{MIUISTORE_ORIGIN}/sph/public/dy-check?{urllib.parse.urlencode({'data': source_url})}"
        checked = json.loads(fetch_text(check_url, allowed_host_suffixes=MIUISTORE_HOST_SUFFIXES))
        _raise_if_cancelled(cancellation_event)
        if checked.get("error") != 0 or not checked.get("url"):
            return None, "unavailable"
        # Follow the path the service handed back instead of rebuilding one.
        # It used to answer with a `dy-r` page and now answers `dy-d`; the
        # hardcoded name turned that rename into HTTP 400, reported as a plain
        # "cannot resolve" (2026-09-03). Whatever it names the next one, this
        # follows it.
        result_url = urllib.parse.urljoin(MIUISTORE_ORIGIN, str(checked["url"]))
        if not urllib.parse.urlparse(result_url).query:
            return None, "invalid_response"
        page_html = fetch_text(result_url, allowed_host_suffixes=MIUISTORE_HOST_SUFFIXES)
        _raise_if_cancelled(cancellation_event)
        links = parse_miuistore_links(page_html)
        download_url = choose_miuistore_video_url(links)
        if not download_url:
            return None, "no_downloadable_media"
        return ResolvedVideo(
            provider="miuistore",
            source_url=source_url,
            download_url=download_url,
            video_id=parse_miuistore_field(page_html, "视频ID"),
            title=parse_miuistore_field(page_html, "视频标题"),
            thumbnail_url=next((href for href in links if "douyinpic.com" in href), None),
        ), None
    except VideoSourceCancelled:
        raise
    except Exception as exc:
        return None, _resolver_failure_reason(exc)


def resolve_with_miuistore(input_text: str) -> ResolvedVideo | None:
    resolved, _ = _resolve_with_miuistore_attempt(input_text)
    return resolved


def resolve_video(
    input_text: str,
    cookies_from_browser: str | None = None,
    *,
    allow_miuistore: bool = True,
    cancellation_event: threading.Event | None = None,
) -> ResolvedVideo:
    _raise_if_cancelled(cancellation_event)
    source_url = extract_first_url(input_text)
    if not source_url:
        raise ValueError("没有识别到视频链接")
    parse_http_url(source_url)
    resolved = resolve_direct_video(source_url)
    if resolved:
        resolved.resolution_trace = [{"provider": "direct", "status": "selected"}]
        return resolved
    if not any(check(source_url) for check in (is_douyin_url, is_bilibili_url, is_youtube_url)):
        raise VideoSourceResolutionError(
            "目前只支持抖音、Bilibili、YouTube 或视频直链",
            [{"provider": "source-policy", "status": "failed", "reason": "unsupported_source"}],
        )
    trace: list[dict[str, str]] = []
    resolved, failure_reason = _resolve_with_yt_dlp_attempt(
        source_url, cookies_from_browser, **_cancellation_kwargs(cancellation_event)
    )
    if resolved:
        resolved.resolution_trace = [{"provider": "yt-dlp", "status": "selected"}]
        return resolved
    trace.append({"provider": "yt-dlp", "status": "failed", "reason": failure_reason or "unavailable"})
    if is_bilibili_url(source_url):
        raise VideoSourceResolutionError(
            "这个 B 站链接需要登录后才能下载。请在设置里选择“用浏览器登录态下载高清”，或改为上传本地视频。",
            trace,
        )
    if not is_douyin_url(source_url):
        raise VideoSourceResolutionError("暂时无法自动解析这个视频链接，请上传视频文件", trace)
    if not allow_miuistore:
        # The fallback runs by default: yt-dlp needs a fresh Douyin login and
        # fails without one, so gating the only working route behind a consent
        # flag meant Douyin links simply did not work. It sends the extracted
        # Douyin URL to a third party, and a caller can still switch it off.
        trace.append({"provider": "miuistore", "status": "skipped", "reason": "disabled_by_request"})
        raise VideoSourceResolutionError("暂时无法自动解析这个视频链接，请上传视频文件", trace)
    resolved, failure_reason = _resolve_with_miuistore_attempt(
        source_url, **_cancellation_kwargs(cancellation_event)
    )
    if resolved:
        trace.append({"provider": "miuistore", "status": "selected"})
        resolved.resolution_trace = trace
        return resolved
    trace.append({"provider": "miuistore", "status": "failed", "reason": failure_reason or "unavailable"})
    raise VideoSourceResolutionError("暂时无法自动解析这个视频链接，请上传视频文件", trace)


def download_referer_for_url(url: str, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if "bilivideo.com" in host or "bilibili.com" in host:
            return "https://www.bilibili.com/"
    except Exception:
        pass
    return "https://www.douyin.com/"


def download_file(
    url: str,
    file_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    referer: str | None = None,
    allowed_host_suffixes: tuple[str, ...] = (),
    cancellation_event: threading.Event | None = None,
) -> int:
    _raise_if_cancelled(cancellation_event)
    effective_host_suffixes = allowed_host_suffixes or trusted_media_host_suffixes(url)
    request = urllib.request.Request(
        url,
        headers={
            "user-agent": "Mozilla/5.0 FluentFlow/1.0",
            "referer": download_referer_for_url(url, referer),
        },
    )
    max_bytes = max_video_bytes()
    downloaded = 0
    try:
        with open_public_http_url(
            request,
            timeout=120,
            allowed_host_suffixes=effective_host_suffixes,
        ) as response:
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > max_bytes:
                raise RuntimeError(f"视频文件过大，当前限制为 {round(max_bytes / 1024 / 1024)}MB")
            on_progress and on_progress(VideoSourceProgress(
                stage="downloading",
                message="正在下载视频" if content_length else "正在下载视频，文件大小未知",
                percent=0 if content_length else None,
                loaded_bytes=0,
                total_bytes=content_length or None,
            ))
            with file_path.open("wb") as output:
                while True:
                    _raise_if_cancelled(cancellation_event)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise RuntimeError(f"视频文件过大，当前限制为 {round(max_bytes / 1024 / 1024)}MB")
                    output.write(chunk)
                    percent = min(99, round(downloaded / content_length * 100)) if content_length else None
                    on_progress and on_progress(VideoSourceProgress(
                        stage="downloading",
                        message="正在下载视频",
                        percent=percent,
                        loaded_bytes=downloaded,
                        total_bytes=content_length or None,
                    ))
    except VideoSourceCancelled:
        file_path.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"视频下载失败：{exc.code}") from exc
    size_bytes = downloaded or file_path.stat().st_size
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message="视频下载完成",
        percent=100,
        loaded_bytes=size_bytes,
        total_bytes=size_bytes,
    ))
    return size_bytes


def merge_media_parts(
    video_url: str,
    audio_url: str,
    file_path: Path,
    *,
    referer: str | None = None,
    cancellation_event: threading.Event | None = None,
) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("B 站音视频需要合并，但当前环境没有找到 ffmpeg")
    with tempfile.TemporaryDirectory(prefix="fluentflow-bili-") as temp_dir:
        temp_path = Path(temp_dir)
        video_path = temp_path / "video.m4s"
        audio_path = temp_path / "audio.m4s"
        download_file(
            video_url,
            video_path,
            referer=referer,
            **_cancellation_kwargs(cancellation_event),
        )
        download_file(
            audio_url,
            audio_path,
            referer=referer,
            **_cancellation_kwargs(cancellation_event),
        )
        output_path = temp_path / "merged.mp4"
        result = _run_process(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-c",
                "copy",
                str(output_path),
            ],
            timeout=180,
            cancellation_event=cancellation_event,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "").strip() or "B 站音视频合并失败")
        shutil.move(str(output_path), file_path)
    return file_path.stat().st_size


def _yt_dlp_cookies_args(cookies_from_browser: str | None = None) -> list[str]:
    value = (cookies_from_browser or os.environ.get("YT_DLP_COOKIES_FROM_BROWSER", "")).strip()
    if value:
        return ["--cookies-from-browser", value]
    # A headless server has no browser profile to read, so a Netscape cookie
    # file is the only way to give it a login (Bilibili hides subtitles from
    # anonymous requests). Ignored unless the file actually exists, so a stale
    # setting degrades to anonymous instead of failing every download.
    cookie_file = (os.environ.get("YT_DLP_COOKIES_FILE") or "").strip()
    if cookie_file and Path(cookie_file).is_file():
        return ["--cookies", cookie_file]
    return []


def yt_dlp_login_configured(cookies_from_browser: str | None = None) -> bool:
    """Whether any cookie source is available for yt-dlp."""

    return bool(_yt_dlp_cookies_args(cookies_from_browser))


def check_browser_cookies(browser: str) -> dict[str, Any]:
    """Read cookies from the given local browser and report whether the login
    state is usable. No network request — it only opens the browser's cookie DB.
    A live download can still fail later (expired cookie, anti-crawl), so this is
    a best-effort check, not a guarantee."""
    browser = (browser or "").strip().lower()
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except Exception as exc:  # pragma: no cover - yt-dlp always installed in prod
        return {"ok": False, "code": "yt_dlp_missing", "message": f"无法加载 yt-dlp 的 cookie 读取模块：{exc}"}
    try:
        jar = extract_cookies_from_browser(browser)
    except Exception as exc:
        return {
            "ok": False,
            "code": "read_failed",
            "message": f"读不到 {browser} 的登录态：{exc}。请确认已安装该浏览器；若仍失败，试试完全关闭该浏览器后重试。",
        }
    cookies = list(jar)
    bilibili_login = any(
        getattr(c, "name", "") == "SESSDATA" and "bilibili" in (getattr(c, "domain", "") or "")
        for c in cookies
    )
    return {"ok": True, "cookie_count": len(cookies), "bilibili_logged_in": bilibili_login}


def youtube_caption_languages() -> str:
    return (os.environ.get("FLUENTFLOW_YOUTUBE_SUB_LANGS") or "en,zh-Hans,zh-Hant,zh").strip()


def bilibili_caption_languages() -> str:
    """Bilibili exposes creator subtitles as zh-* and its machine ones as ai-zh."""

    return (os.environ.get("FLUENTFLOW_BILIBILI_SUB_LANGS") or "zh-Hans,zh-CN,zh,ai-zh").strip()


def caption_language_candidates(provider: str = "youtube") -> list[str]:
    raw = bilibili_caption_languages() if provider == "bilibili" else youtube_caption_languages()
    return [item.strip() for item in raw.split(",") if item.strip()] or ["en"]


def youtube_caption_language_candidates() -> list[str]:
    return caption_language_candidates("youtube")


def caption_provider(url: str) -> str | None:
    """Which caption source, if any, can serve this URL without downloading media."""

    try:
        if is_youtube_url(url):
            return "youtube"
        if is_bilibili_url(url):
            return "bilibili"
    except Exception:
        return None
    return None


def captions_first_provider(url: str, cookies_from_browser: str | None = None) -> str | None:
    """The caption source to try before falling back to downloading media.

    Bilibili is gated on having a login: it refuses subtitles to anonymous
    requests ("Subtitles are only available when logged in"), so attempting it
    without cookies is a guaranteed-failed round trip that only adds latency
    before the media download we were going to do anyway.
    """

    provider = caption_provider(url)
    if provider == "bilibili" and not yt_dlp_login_configured(cookies_from_browser):
        return None
    return provider


def download_timeout_seconds(
    *,
    duration_seconds: float | None = None,
    estimated_size_bytes: int | None = None,
) -> int:
    override = os.environ.get("FLUENTFLOW_YT_DLP_DOWNLOAD_TIMEOUT_SECONDS")
    if override:
        try:
            return max(int(float(override)), 60)
        except ValueError:
            pass
    try:
        max_timeout = max(int(float(os.environ.get("FLUENTFLOW_YT_DLP_MAX_TIMEOUT_SECONDS", "3600"))), 300)
    except ValueError:
        max_timeout = 3600
    budgets = [900]
    if duration_seconds:
        budgets.append(180 + int(float(duration_seconds) * 0.22))
    if estimated_size_bytes:
        budgets.append(180 + int(int(estimated_size_bytes) / (256 * 1024)))
    return min(max(budgets), max_timeout)


def video_source_failure_reason(error: Any) -> str:
    text = str(error or "").lower()
    if "no module named yt_dlp" in text or "no module named 'yt_dlp'" in text:
        return "dependency_missing"
    if "fresh cookies" in text or "cookies are needed" in text:
        return "fresh_cookies_required"
    if "目前只支持抖音、bilibili、youtube 或视频直链" in text:
        return "unsupported_source"
    if "必须解析到公网地址" in text or "不能访问本机、内网或云元数据服务" in text:
        return "unsafe_url"
    if "po token" in text or "sabr streaming" in text or "the page needs to be reloaded" in text:
        return "youtube_media_restricted"
    if "http error 403" in text or "forbidden" in text or "视频下载失败：403" in text:
        return "forbidden"
    if "http error 429" in text or "too many requests" in text:
        return "rate_limited"
    if "timed out" in text or "timeout" in text or "视频下载超时" in text:
        return "timeout"
    if "too large" in text or "文件过大" in text or "file is too large" in text:
        return "too_large"
    if "没有可用字幕" in text or "no subtitles" in text or "no captions" in text:
        return "no_captions"
    return "unknown"


CAPTION_PROVIDER_LABELS = {"youtube": "YouTube", "bilibili": "B 站"}
# Spelled out per provider so Chinese spacing reads correctly in both.
CAPTION_UNAVAILABLE_LABELS = {"youtube": "YouTube 字幕", "bilibili": "B 站字幕"}


def download_source_captions(
    url: str,
    file_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    provider: str | None = None,
    cookies_from_browser: str | None = None,
    cancellation_event: threading.Event | None = None,
) -> int:
    """Fetch existing subtitles instead of the media, skipping STT entirely."""

    _raise_if_cancelled(cancellation_event)
    parse_http_url(url)
    resolved_provider = provider or caption_provider(url)
    if resolved_provider not in CAPTION_PROVIDER_LABELS:
        raise RuntimeError("这个来源不支持直接获取字幕")
    label = CAPTION_PROVIDER_LABELS[resolved_provider]
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message=f"正在获取 {label} 字幕",
        percent=None,
        loaded_bytes=None,
        total_bytes=None,
    ))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for language in caption_language_candidates(resolved_provider):
        _raise_if_cancelled(cancellation_event)
        with tempfile.TemporaryDirectory(prefix="fluentflow-captions-") as temp_dir:
            output_template = str(Path(temp_dir) / "captions.%(ext)s")
            args = [
                sys.executable,
                "-m",
                "yt_dlp",
                "--skip-download",
                "--no-playlist",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                language,
                "--sub-format",
                "srt",
                "-o",
                output_template,
                *_caption_extractor_args(resolved_provider),
                *_yt_dlp_cookies_args(cookies_from_browser),
                url,
            ]
            result = _run_process(
                args,
                timeout=120,
                cancellation_event=cancellation_event,
            )
            candidates = sorted(Path(temp_dir).glob("captions*.srt"))
            if result.returncode == 0 and candidates:
                shutil.move(str(candidates[0]), file_path)
                break
            errors.append((result.stderr or result.stdout or f"{language}: yt-dlp 字幕下载失败，退出码 {result.returncode}").strip())
    if not file_path.is_file():
        detail = errors[-1] if errors else f"这个{label}视频没有可用字幕"
        raise RuntimeError(detail or f"这个{label}视频没有可用字幕")
    size_bytes = file_path.stat().st_size
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message=f"{label} 字幕获取完成",
        percent=100,
        loaded_bytes=size_bytes,
        total_bytes=size_bytes,
    ))
    return size_bytes


def _caption_extractor_args(provider: str) -> list[str]:
    if provider == "youtube":
        return ["--extractor-args", "youtube:player_client=android"]
    if provider == "bilibili":
        # Same anti-crawl headers the metadata probe uses; without them Bilibili
        # answers HTTP 412.
        return [
            "--add-header",
            "Referer:https://www.bilibili.com/",
            "--add-header",
            "Origin:https://www.bilibili.com",
            "--add-header",
            f"User-Agent:{BILIBILI_USER_AGENT}",
        ]
    return []


def download_yt_dlp_media(
    url: str,
    file_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    duration_seconds: float | None = None,
    estimated_size_bytes: int | None = None,
    cookies_from_browser: str | None = None,
    cancellation_event: threading.Event | None = None,
) -> int:
    _raise_if_cancelled(cancellation_event)
    parse_http_url(url)
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message="正在下载视频",
        percent=None,
        loaded_bytes=None,
        total_bytes=None,
    ))
    max_bytes = max_video_bytes()
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-part",
        "--force-overwrites",
        "--no-progress",
        "--max-filesize",
        str(max_bytes),
        "-f",
        "best[ext=mp4]/best",
        "-o",
        str(file_path),
        *_yt_dlp_cookies_args(cookies_from_browser),
    ]
    try:
        if is_youtube_url(url):
            args.extend(["--extractor-args", "youtube:player_client=android"])
    except Exception:
        pass
    args.append(url)
    timeout = download_timeout_seconds(
        duration_seconds=duration_seconds,
        estimated_size_bytes=estimated_size_bytes,
    )
    try:
        result = _run_process(
            args,
            timeout=timeout,
            cancellation_event=cancellation_event,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"视频下载超时：视频可能较大或当前网络较慢，已等待 {timeout} 秒。") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "").strip() or f"yt-dlp 下载失败，退出码 {result.returncode}")
    size_bytes = file_path.stat().st_size
    if size_bytes > max_bytes:
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(f"视频文件过大，当前限制为 {round(max_bytes / 1024 / 1024)}MB")
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message="视频下载完成",
        percent=100,
        loaded_bytes=size_bytes,
        total_bytes=size_bytes,
    ))
    return size_bytes


def write_source_info(video_dir: Path, title: str, source_url: str) -> None:
    metadata_path = video_dir / SOURCE_INFO_FILE
    try:
        current = metadata_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if source_url in current:
        return
    prefix = "\n" if current.strip() else ""
    metadata_path.write_text(f"{current}{prefix}{title} {source_url}\n", encoding="utf-8")


def write_json_metadata(file_path: Path, metadata: dict[str, Any]) -> Path:
    metadata_path = file_path.with_suffix(".source.json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def build_asset_strategy(
    *,
    media_type: str,
    source_url: str,
    file_path: Path,
    file_url: str,
    filename: str,
    caption_failure_reason: str | None = None,
    caption_source: str | None = None,
) -> dict[str, Any]:
    if media_type == "transcript":
        return {
            "transcript_asset": {
                "status": "completed",
                "kind": "subtitle",
                "source": f"{caption_source or caption_provider(source_url) or 'youtube'}_captions",
                "filename": filename,
                "file_path": str(file_path),
                "file_url": file_url,
            },
            "playback_asset": {
                "status": "available",
                "playback_mode": "external_url",
                "source_url": source_url,
            },
            "visual_asset": {
                "status": "unavailable",
                "reason": "local_video_not_downloaded",
            },
            "download_status": "skipped",
            "failure_reason": None,
        }
    return {
        "transcript_asset": {
            "status": "pending",
            "kind": "stt_from_media",
        },
        "playback_asset": {
            "status": "completed",
            "playback_mode": "local_file",
            "filename": filename,
            "file_path": str(file_path),
            "file_url": file_url,
        },
        "visual_asset": {
            "status": "pending",
            "source": "local_video",
        },
        "download_status": "completed",
        "failure_reason": caption_failure_reason,
    }


def download_video_source(
    input_text: str,
    *,
    title: str | None = None,
    video_dir: Path,
    on_progress: ProgressCallback | None = None,
    cookies_from_browser: str | None = None,
    allow_miuistore: bool = True,
    cancellation_event: threading.Event | None = None,
) -> SavedVideoSource:
    _raise_if_cancelled(cancellation_event)
    normalized = (input_text or "").strip()
    if not normalized:
        raise ValueError("缺少视频分享文本或视频链接")
    if len(normalized) > 4000:
        raise ValueError("分享文本过长")

    video_dir.mkdir(parents=True, exist_ok=True)
    on_progress and on_progress(VideoSourceProgress(stage="resolving", message="正在解析分享链接", percent=8))
    resolved = resolve_video(
        normalized,
        cookies_from_browser,
        allow_miuistore=allow_miuistore,
        **_cancellation_kwargs(cancellation_event),
    )
    caption_source = (
        captions_first_provider(resolved.source_url, cookies_from_browser)
        if resolved.provider == "yt-dlp"
        else None
    )
    media_type = "transcript" if caption_source else "video"
    extension = ".srt" if media_type == "transcript" else ".mp4"
    video_id, raw_title, display_title, filename = resolve_filename(resolved, title, extension=extension)
    file_path = video_dir / filename
    caption_failure_reason: str | None = None
    downloaded_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        size_bytes = file_path.stat().st_size
        on_progress and on_progress(VideoSourceProgress(
            stage="downloading",
            message="本地已有视频文件",
            percent=100,
            loaded_bytes=size_bytes,
            total_bytes=size_bytes,
        ))
    except FileNotFoundError:
        # Captions come first when the source has them: no media download, no STT,
        # so the transcription cost for that task is zero. Any failure downgrades
        # to the media path below rather than failing the task.
        if media_type == "transcript":
            try:
                size_bytes = download_source_captions(
                    resolved.source_url,
                    file_path,
                    on_progress,
                    provider=caption_source,
                    cookies_from_browser=cookies_from_browser,
                    **_cancellation_kwargs(cancellation_event),
                )
            except VideoSourceCancelled:
                raise
            except Exception as exc:
                caption_failure_reason = video_source_failure_reason(exc)
                media_type = "video"
                video_id, raw_title, display_title, filename = resolve_filename(resolved, title)
                file_path = video_dir / filename

        if media_type == "video":
            caption_label = CAPTION_UNAVAILABLE_LABELS.get(caption_source or "", "字幕")
            try:
                if resolved.audio_url and resolved.download_url:
                    on_progress and on_progress(VideoSourceProgress(
                        stage="downloading",
                        message="正在下载并合并 B 站音视频",
                        percent=None,
                    ))
                    size_bytes = merge_media_parts(
                        resolved.download_url,
                        resolved.audio_url,
                        file_path,
                        referer=resolved.referer,
                        **_cancellation_kwargs(cancellation_event),
                    )
                    on_progress and on_progress(VideoSourceProgress(
                        stage="downloading",
                        message="视频下载完成",
                        percent=100,
                        loaded_bytes=size_bytes,
                        total_bytes=size_bytes,
                    ))
                elif resolved.provider == "yt-dlp":
                    size_bytes = download_yt_dlp_media(
                        resolved.source_url,
                        file_path,
                        on_progress,
                        duration_seconds=resolved.duration_seconds,
                        estimated_size_bytes=resolved.estimated_size_bytes,
                        cookies_from_browser=cookies_from_browser,
                        **_cancellation_kwargs(cancellation_event),
                    )
                elif resolved.referer:
                    size_bytes = download_file(
                        resolved.download_url,
                        file_path,
                        on_progress,
                        referer=resolved.referer,
                        **_cancellation_kwargs(cancellation_event),
                    )
                elif resolved.provider == "miuistore":
                    size_bytes = download_file(
                        resolved.download_url,
                        file_path,
                        on_progress,
                        allowed_host_suffixes=DOUYIN_MEDIA_HOST_SUFFIXES,
                        **_cancellation_kwargs(cancellation_event),
                    )
                else:
                    size_bytes = download_file(
                        resolved.download_url,
                        file_path,
                        on_progress,
                        **_cancellation_kwargs(cancellation_event),
                    )
            except VideoSourceCancelled:
                raise
            except Exception as media_exc:
                if caption_failure_reason:
                    raise RuntimeError(
                        f"{caption_label}不可用，且原视频下载失败：{media_exc}"
                    ) from media_exc
                raise

    _raise_if_cancelled(cancellation_event)
    on_progress and on_progress(VideoSourceProgress(stage="saving", message="正在保存视频信息", percent=96))
    file_url = f"/video-sources/files/{urllib.parse.quote(filename)}"
    asset_strategy = build_asset_strategy(
        media_type=media_type,
        source_url=resolved.source_url,
        file_path=file_path,
        file_url=file_url,
        filename=filename,
        caption_failure_reason=caption_failure_reason,
        caption_source=caption_source,
    )
    metadata = {
        "provider": resolved.provider,
        "source_url": resolved.source_url,
        "download_url": resolved.download_url,
        "audio_url": resolved.audio_url,
        "referer": resolved.referer,
        "duration_seconds": resolved.duration_seconds,
        "estimated_size_bytes": resolved.estimated_size_bytes,
        "video_id": video_id,
        "raw_title": raw_title,
        "display_title": display_title,
        "title": display_title,
        "filename": filename,
        "file_path": str(file_path),
        "file_url": file_url,
        "size_bytes": size_bytes,
        "downloaded_at": downloaded_at,
        "media_type": media_type,
        "asset_strategy": asset_strategy,
        "resolution_trace": resolved.resolution_trace,
    }
    metadata_path = write_json_metadata(file_path, metadata)
    write_source_info(video_dir, display_title, resolved.source_url)
    return SavedVideoSource(ok=True, metadata_path=str(metadata_path), **metadata)
