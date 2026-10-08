"""Resolve and download shared video links for FluentFlow."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.local_limits_config import max_upload_mb
from backend.core.title_display import display_title_for_user

logger = logging.getLogger(__name__)

SOURCE_INFO_FILE = "视频链接相关信息.md"
# A URL is printable ASCII. Share text glues Chinese straight onto the link
# ("https://v.douyin.com/XRII7O_AcDI/复制此链接"), and a pattern that only stopped
# at Chinese punctuation swallowed those words into the link.
_URL_CHARS = r"[\x21\x23-\x26\x28-\x3b\x3d\x3f-\x7e]"
URL_RE = re.compile(rf"https?://{_URL_CHARS}+", re.I)
# Share text from the apps sometimes carries the short link without a scheme.
_BARE_SHORT_LINK_RE = re.compile(
    rf"(?<![\w./@:-])((?:v\.douyin\.com|b23\.tv|bili2233\.cn|youtu\.be)/{_URL_CHARS}+)", re.I
)
MIUISTORE_ORIGIN = "https://sph.miuistore.com"
MIUISTORE_HOST_SUFFIXES = ("miuistore.com",)
# The resolver's result page has, since about 2026-10, been an empty shell that
# loads the real result from this API host with a script call. Only that call's
# URL is ever fetched from it; it carries the resolver's own encrypted video id.
MIUISTORE_API_HOST_SUFFIXES = ("convry.com",)
_MIUISTORE_INNER_RESULT = re.compile(r"""\$\.get\(\s*["'](https://[^"']+/dy-r\?[^"']+)["']""")
DOUYIN_PAGE_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")
BILIBILI_PAGE_HOST_SUFFIXES = ("bilibili.com", "b23.tv", "bili2233.cn")
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
    # The browser's cookie store could not be read during resolution; later
    # yt-dlp steps for this link go without cookies instead of failing again.
    cookies_unreadable: bool = False


@dataclass
class CookieUse:
    """What happened to the browser login across one link's yt-dlp steps."""

    sent: bool = False
    unreadable: bool = False
    notes: list[dict[str, str]] = field(default_factory=list)


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
    on_output_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a child process, stopping it and its children on cancel or timeout.

    The child is started in its own session so that stopping it stops the whole
    process group: yt-dlp runs ffmpeg as a child of its own to merge or remux,
    and terminating yt-dlp alone left that ffmpeg writing into the download
    after the user had cancelled the task.
    """
    _raise_if_cancelled(cancellation_event)
    if on_output_line is not None:
        return _run_process_streaming(
            args, timeout=timeout, cancellation_event=cancellation_event, on_output_line=on_output_line
        )
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while True:
        if cancellation_event is not None and cancellation_event.is_set():
            _stop_process_group(process)
            raise VideoSourceCancelled("Video source operation cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stdout, stderr = _stop_process_group(process)
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        try:
            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _run_process_streaming(
    args: list[str],
    *,
    timeout: float,
    cancellation_event: threading.Event | None,
    on_output_line: Callable[[str], None],
) -> subprocess.CompletedProcess[str]:
    """``_run_process`` that hands each stdout line to ``on_output_line`` as it
    is written, so a download can report its progress while it runs."""
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def pump(stream, sink: list[str], callback: Callable[[str], None] | None) -> None:
        for line in iter(stream.readline, ""):
            sink.append(line)
            if callback is not None:
                try:
                    callback(line.rstrip("\r\n"))
                except Exception:  # noqa: BLE001 - a progress report must not stop the download
                    logger.debug("progress callback failed", exc_info=True)
        stream.close()

    readers = [
        threading.Thread(target=pump, args=(process.stdout, stdout_lines, on_output_line), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_lines, None), daemon=True),
    ]
    for reader in readers:
        reader.start()

    def finish(wait: float) -> None:
        for reader in readers:
            reader.join(timeout=wait)

    deadline = time.monotonic() + timeout
    while True:
        if cancellation_event is not None and cancellation_event.is_set():
            _signal_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _signal_process_group(process, signal.SIGKILL)
                process.wait()
            finish(1)
            raise VideoSourceCancelled("Video source operation cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _signal_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _signal_process_group(process, signal.SIGKILL)
                process.wait()
            finish(1)
            raise subprocess.TimeoutExpired(
                args, timeout, output="".join(stdout_lines), stderr="".join(stderr_lines)
            )
        try:
            process.wait(timeout=min(0.2, remaining))
        except subprocess.TimeoutExpired:
            continue
        finish(5)
        return subprocess.CompletedProcess(args, process.returncode, "".join(stdout_lines), "".join(stderr_lines))


def _signal_process_group(process: subprocess.Popen, sig: int) -> None:
    """Send ``sig`` to the child's whole group, or to the child alone if the
    group is already gone or this platform has no process groups."""
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()
        except OSError:
            pass


def _stop_process_group(process: subprocess.Popen) -> tuple[str, str]:
    """Terminate, then kill, the child and everything it started. Returns what
    the child had written so a timeout can still report it."""
    _signal_process_group(process, signal.SIGTERM)
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        return process.communicate()


# The scratch name a yt-dlp download is written under until it has finished.
PARTIAL_DOWNLOAD_SUFFIX = ".download"


def max_video_bytes() -> int:
    """The largest file a link may download: the same limit an upload has.

    A separate, smaller link limit (it was 600MB against 2048MB for uploads)
    refused links whose file the user could then upload by hand without
    trouble. ``VIDEO_SOURCE_MAX_BYTES`` still overrides it.
    """
    try:
        parsed = int(os.environ.get("VIDEO_SOURCE_MAX_BYTES", ""))
        if parsed > 0:
            return parsed
    except ValueError:
        pass
    return int(max_upload_mb() * 1024 * 1024)


def _megabytes(size_bytes: float) -> str:
    return f"{size_bytes / 1024 / 1024:.0f}"


def link_too_large_message(size_bytes: float | None, limit_bytes: int, *, estimated: bool = False) -> str:
    """What the user reads when a link's video is over the size limit."""
    size_text = f"{'约 ' if estimated else ''}{_megabytes(size_bytes)} MB" if size_bytes else "超过上限"
    return (
        f"链接视频过大：这个视频{size_text}，链接下载的上限是 {_megabytes(limit_bytes)} MB。"
        "请先下载到本机，压缩或拆分后再上传。"
    )


def link_too_long_message(duration_seconds: float, limit_seconds: float) -> str:
    return (
        f"视频时长过长：约 {duration_seconds / 60:.1f} 分钟，当前限制为 {limit_seconds / 60:.1f} 分钟。"
        "请先下载到本机，拆分后再上传。"
    )


_TRAILING_URL_PUNCTUATION = ".,;:!?)]}>\"'"


def trim_url(value: str) -> str:
    return value.strip().rstrip(_TRAILING_URL_PUNCTUATION + ")）]】“”‘’。，")


def extract_urls(input_text: str) -> list[str]:
    """Every link in the text, in the order it appears.

    Bare short links (``v.douyin.com/…``, ``b23.tv/…``) get ``https://``.
    """
    text = input_text or ""
    found: list[tuple[int, str]] = []
    for match in URL_RE.finditer(text):
        found.append((match.start(), trim_url(match.group(0))))
    for match in _BARE_SHORT_LINK_RE.finditer(text):
        found.append((match.start(1), "https://" + trim_url(match.group(1))))
    urls: list[str] = []
    for _, url in sorted(found):
        if url and url not in urls and urllib.parse.urlparse(url).hostname:
            urls.append(url)
    return urls


def is_supported_source_url(url: str) -> bool:
    return any(check(url) for check in (is_douyin_url, is_bilibili_url, is_youtube_url, is_probably_direct_video_url))


def extract_first_url(input_text: str) -> str | None:
    """The link to fetch: the first one FluentFlow supports, else the first one.

    Share text can carry more than one link (a shop link before the video, two
    videos pasted together); only one is fetched, and ``extra_urls_ignored``
    says whether others were dropped.
    """
    urls = extract_urls(input_text)
    if not urls:
        return None
    return next((url for url in urls if is_supported_source_url(url)), urls[0])


def extra_urls_ignored(input_text: str) -> bool:
    return len(extract_urls(input_text)) > 1


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
    allow_proxy_fake_ip: bool = False,
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
    proxy_fake_ip_allowed = bool(allowed_host_suffixes) or allow_proxy_fake_ip
    if not addresses or any(
        (not address.is_global or address.is_multicast)
        and not (proxy_fake_ip_allowed and address in PROXY_FAKE_IP_NETWORK)
        for address in addresses
    ):
        raise ValueError("视频链接必须解析到公网地址，不能访问本机、内网或云元数据服务")
    return parsed


class PublicHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects that leave the public network or an optional host allowlist.

    ``any_public_host`` lets a redirect go to any public host while still
    refusing private addresses, other ports and other schemes. It is for a
    download that started on a trusted media host: Douyin's CDN hands some
    files to a partner CDN (seen 2026-10-08: a 302 from a douyinvod.com host to
    ``*.v1d.pkoplink.com``), and refusing that host failed about a third of
    Douyin downloads.
    """

    def __init__(self, allowed_host_suffixes: tuple[str, ...] = (), *, any_public_host: bool = False) -> None:
        super().__init__()
        self.allowed_host_suffixes = allowed_host_suffixes
        self.any_public_host = any_public_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.any_public_host:
            validate_public_http_url(newurl, allow_proxy_fake_ip=True)
        else:
            validate_public_http_url(newurl, allowed_host_suffixes=self.allowed_host_suffixes)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public_http_url(
    request_or_url: urllib.request.Request | str,
    *,
    timeout: float,
    allowed_host_suffixes: tuple[str, ...] = (),
    redirect_to_any_public_host: bool = False,
):
    url = request_or_url.full_url if isinstance(request_or_url, urllib.request.Request) else request_or_url
    validate_public_http_url(url, allowed_host_suffixes=allowed_host_suffixes)
    opener = urllib.request.build_opener(
        PublicHTTPRedirectHandler(allowed_host_suffixes, any_public_host=redirect_to_any_public_host)
    )
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
        if is_bilibili_url(source_url):
            return f"Bilibili 视频 {video_id}" if video_id else "Bilibili 视频"
        if is_douyin_url(source_url):
            return "抖音视频链接"
        if "youtube.com" in host or host == "youtu.be":
            return "YouTube 视频"
        if host:
            return f"{host} 视频链接"
    except Exception:
        pass
    return display_title_for_user(fallback or input_text, fallback or input_text) or "视频链接"


_HASHTAG_TAIL_RE = re.compile(r"(?:\s*#[^\s#]+)+\s*$")
_HASHTAG_RE = re.compile(r"#([^\s#]+)")


def strip_hashtags(title: str | None) -> str | None:
    """Drop the trailing ``#话题`` run Douyin appends to a caption.

    Hashtags inside the sentence keep their word and lose the ``#``. A caption
    made only of hashtags keeps the words, which is better than no title.
    """
    if not title:
        return title
    stripped = _HASHTAG_TAIL_RE.sub("", title).strip()
    if not stripped:
        stripped = title
    return re.sub(r"\s+", " ", _HASHTAG_RE.sub(r"\1", stripped)).strip()


def resolve_filename(video: ResolvedVideo, requested_title: str | None = None, extension: str = ".mp4") -> tuple[str, str, str, str]:
    video_id = sanitize_filename_part(video.video_id or video_id_from_url(video.source_url)) or stable_id(video.source_url)
    source_title = video.title
    if source_title and (video.provider == "miuistore" or is_douyin_url(video.source_url)):
        source_title = strip_hashtags(source_title)
    raw_title = sanitize_filename_part(requested_title or source_title or f"视频-{video_id}") or f"视频-{video_id}"
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


def _url_host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def is_bilibili_url(url: str) -> bool:
    return _host_matches_suffixes(_url_host(url), BILIBILI_PAGE_HOST_SUFFIXES)


def is_douyin_url(url: str) -> bool:
    return _host_matches_suffixes(_url_host(url), DOUYIN_PAGE_HOST_SUFFIXES)


_DOUYIN_SHARE_PATH = re.compile(r"^/share/(video|note|slides)/(\d+)")


def normalize_douyin_url(url: str) -> str:
    """``iesdouyin.com/share/video/<id>`` is the same video as
    ``douyin.com/video/<id>``; only the latter is understood by yt-dlp."""
    parsed = urllib.parse.urlparse(url)
    if _host_matches_suffixes(_url_host(url), ("iesdouyin.com",)):
        match = _DOUYIN_SHARE_PATH.match(parsed.path or "")
        if match:
            return f"https://www.douyin.com/{match.group(1)}/{match.group(2)}"
    return url


def is_douyin_image_post_url(url: str) -> bool:
    """A Douyin image post (图文): pictures and music, nothing to transcribe."""
    if not is_douyin_url(url):
        return False
    path = urllib.parse.urlparse(url).path or ""
    return bool(re.search(r"/(note|slides)/", path))


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


def _direct_link_title(url: str) -> str | None:
    """A direct link's own file name is the best title it has."""
    try:
        name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path or "")).stem
    except Exception:
        return None
    return name.strip() or None


def resolve_direct_video(url: str) -> ResolvedVideo | None:
    if not is_probably_direct_video_url(url):
        return None
    return ResolvedVideo(
        provider="direct",
        source_url=url,
        download_url=url,
        video_id=video_id_from_url(url),
        title=_direct_link_title(url) if urllib.parse.urlparse(url).path.lower().endswith(".mp4") else None,
    )


# A browser name alone makes yt-dlp read that browser's most recently used
# profile, which is often not the one logged in to the site: measured 2026-09-23
# on a Chrome with four profiles, the default pick held no Douyin cookies and
# every Douyin link failed "fresh cookies are needed", while naming either of
# the two profiles that had visited Douyin resolved the same link at once.
_SITE_COOKIE_DOMAINS = (
    (("douyin.com", "iesdouyin.com"), "douyin.com"),
    (("bilibili.com", "b23.tv", "bili2233.cn"), "bilibili.com"),
    (("youtube.com", "youtu.be"), "youtube.com"),
)
_PROFILE_CHOICE_TTL_SECONDS = 600.0
_profile_choice_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _cookie_domain_for_url(url: str) -> str | None:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for hosts, domain in _SITE_COOKIE_DOMAINS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return domain
    return None


def browser_cookie_spec(browser: str | None, url: str) -> str | None:
    """The ``--cookies-from-browser`` value to use for ``url``.

    For a browser with profiles, named without one, picks the profile holding
    the most cookies for the link's site. Anything already naming a profile, a
    browser without profiles, or a lookup that fails is passed through as given.
    """
    value = (browser or "").strip()
    if not value or ":" in value:
        return value or None
    domain = _cookie_domain_for_url(url)
    if not domain:
        return value
    key = (value.lower(), domain)
    cached = _profile_choice_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _PROFILE_CHOICE_TTL_SECONDS:
        return cached[1]
    choice = value
    try:
        from yt_dlp.cookies import (
            CHROMIUM_BASED_BROWSERS,
            _get_chromium_based_browser_settings,
            extract_cookies_from_browser,
        )

        if value.lower() in CHROMIUM_BASED_BROWSERS:
            settings = _get_chromium_based_browser_settings(value.lower())
            root = Path(settings["browser_dir"])
            profiles = sorted(
                p.name for p in root.iterdir()
                if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
            ) if root.is_dir() else []
            best, best_count = None, 0
            for profile in profiles:
                try:
                    jar = extract_cookies_from_browser(value.lower(), profile)
                except Exception:  # noqa: BLE001 - one unreadable profile must not stop the rest
                    continue
                count = sum(1 for c in jar if domain in (getattr(c, "domain", "") or ""))
                if count > best_count:
                    best, best_count = profile, count
            if best:
                choice = f"{value}:{best}"
    except Exception:  # noqa: BLE001 - fall back to yt-dlp's own pick
        logger.debug("browser profile lookup failed for %s", value, exc_info=True)
    _profile_choice_cache[key] = (now, choice)
    return choice


# Bilibili and YouTube list every quality up to 4K; transcription needs the
# sound and a picture good enough for keyframes. Capping at 720p keeps a long
# talk well under the size limit (measured 2026-10-08: a YouTube link that hit
# the old 600MB limit at its best quality came down as 720p with audio in 4s).
CAPPED_FORMAT = "bv*[height<=720]+ba/b[height<=720]/b"
DOUYIN_FORMAT = "best[ext=mp4]/best"


def yt_dlp_format_for(url: str) -> str:
    return CAPPED_FORMAT if (is_bilibili_url(url) or is_youtube_url(url)) else DOUYIN_FORMAT


def _site_args(url: str) -> list[str]:
    """Per-site arguments every yt-dlp call for ``url`` needs."""
    if is_youtube_url(url):
        return ["--extractor-args", "youtube:player_client=android"]
    if is_bilibili_url(url):
        # Without these Bilibili answers HTTP 412.
        return [
            "--add-header",
            "Referer:https://www.bilibili.com/",
            "--add-header",
            "Origin:https://www.bilibili.com",
            "--add-header",
            f"User-Agent:{BILIBILI_USER_AGENT}",
        ]
    return []


def _run_yt_dlp_step(
    build_args: Callable[[list[str]], list[str]],
    *,
    url: str,
    cookies_from_browser: str | None,
    cookie_use: CookieUse | None,
    timeout: float,
    cancellation_event: threading.Event | None = None,
    on_output_line: Callable[[str], None] | None = None,
    before_retry: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one yt-dlp step with the browser login, and again without it when
    the browser's cookie store cannot be read.

    An unreadable store makes yt-dlp exit before it touches the site (macOS 27
    stopped letting other programs read Chrome's data), so every link on every
    platform failed while the same link worked without a login. The retry is
    noted in ``cookie_use`` and later steps skip the login altogether.
    """
    use = cookie_use if cookie_use is not None else CookieUse()
    cookie_args = [] if use.unreadable else _yt_dlp_cookies_args(cookies_from_browser, url)
    use.sent = bool(cookie_args)
    extra = {"on_output_line": on_output_line} if on_output_line is not None else {}
    result = _run_process(build_args(cookie_args), timeout=timeout, cancellation_event=cancellation_event, **extra)
    if (
        cookie_args
        and result.returncode != 0
        and _cookie_store_unreadable(f"{result.stderr or ''} {result.stdout or ''}")
    ):
        logger.warning(
            "browser cookies for %s could not be read; retrying without them: %s",
            url, _failure_detail(result.stderr or result.stdout or ""),
        )
        if not use.unreadable:
            use.notes.append({
                "provider": "browser-cookies",
                "status": "unreadable",
                "reason": "cookie_store_unreadable",
                "detail": "读不到浏览器登录态，已改为不带登录重试",
            })
        use.unreadable = True
        use.sent = False
        if before_retry is not None:
            before_retry()
        result = _run_process(build_args([]), timeout=timeout, cancellation_event=cancellation_event, **extra)
    return result


def run_yt_dlp(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
    cookie_use: CookieUse | None = None,
) -> dict[str, Any]:
    def build(cookie_args: list[str]) -> list[str]:
        args = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--no-playlist"]
        if is_bilibili_url(url) or is_youtube_url(url):
            # The same format the download will pick, so the size estimate
            # is the size of the file we are about to fetch.
            args.extend(["-f", CAPPED_FORMAT])
        return [*args, *cookie_args, *_site_args(url), url]

    result = _run_yt_dlp_step(
        build,
        url=url,
        cookies_from_browser=cookies_from_browser,
        cookie_use=cookie_use,
        timeout=90,
        cancellation_event=cancellation_event,
    )
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
    """The size of the format yt-dlp selected, when it says.

    Only the selected format counts: the largest of all listed formats is a 4K
    stream nobody downloads, and refusing a link on that number would refuse
    links that fit.
    """
    direct = _number_or_none(info.get("filesize") or info.get("filesize_approx"))
    if direct:
        return int(direct)
    requested = info.get("requested_formats") or []
    sizes = [
        _number_or_none(item.get("filesize") or item.get("filesize_approx"))
        for item in requested
        if isinstance(item, dict)
    ]
    if requested and all(sizes):
        return int(sum(size for size in sizes if size))
    return None


def _resolver_failure_reason(error: Exception) -> str:
    reason = video_source_failure_reason(error)
    return reason if reason != "unknown" else "unavailable"


# Douyin answers "fresh cookies are needed" to a share link intermittently, with
# no pattern and no change on our side: measured 2026-09-03 on one link, the same
# call alternated between a full JSON answer and that refusal within seconds.
# Retried only for that reason, and only when a browser login was actually
# sent: without one the refusal is what Douyin always says (measured
# 2026-10-08, four of four attempts), so retrying only delayed the third-party
# fallback by a fixed dozen seconds.
_TRANSIENT_RESOLVER_REASONS = frozenset({"fresh_cookies_required"})
_RESOLVER_RETRIES = 3
# Escalating, because the refusal looks like throttling: measured on one link,
# two attempts 2s apart still lost one run in three, while backing off further
# recovered it.
_RESOLVER_RETRY_SLEEP_SECONDS = 2.0


def _failure_detail(error: Exception | str, limit: int = 400) -> str:
    """The tail of what the tool said, for the trace and the log. The reason code
    says which category a failure fell into; this says what actually happened."""
    text = " ".join(str(error or "").split())
    return text[-limit:] if len(text) > limit else text


def _pause_before_retry(attempt: int, cancellation_event: threading.Event | None) -> None:
    _raise_if_cancelled(cancellation_event)
    pause = _RESOLVER_RETRY_SLEEP_SECONDS * attempt
    if cancellation_event is not None:
        if cancellation_event.wait(pause):
            _raise_if_cancelled(cancellation_event)
    else:
        time.sleep(pause)


def _should_retry(reason: str | None, cookie_use: CookieUse) -> bool:
    return reason in _TRANSIENT_RESOLVER_REASONS and cookie_use.sent


def _resolve_with_yt_dlp_attempt(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
    cookie_use: CookieUse | None = None,
) -> tuple[ResolvedVideo | None, str | None, str | None]:
    """Resolve through yt-dlp, retrying only the one transient refusal.

    Returns the video, or the failure's reason code and the tail of yt-dlp's
    own message. The reason is what the product branches on; the detail is
    what makes a failed link diagnosable afterwards.
    """
    use = cookie_use if cookie_use is not None else CookieUse()
    resolved, reason, detail = _resolve_with_yt_dlp_once(
        url, cookies_from_browser, cancellation_event=cancellation_event, cookie_use=use
    )
    attempts = 0
    while resolved is None and _should_retry(reason, use) and attempts < _RESOLVER_RETRIES:
        attempts += 1
        _pause_before_retry(attempts, cancellation_event)
        resolved, reason, detail = _resolve_with_yt_dlp_once(
            url, cookies_from_browser, cancellation_event=cancellation_event, cookie_use=use
        )
    return resolved, reason, detail


def _resolve_with_yt_dlp_once(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
    cookie_use: CookieUse | None = None,
) -> tuple[ResolvedVideo | None, str | None, str | None]:
    try:
        kwargs: dict[str, Any] = dict(_cancellation_kwargs(cancellation_event))
        if cookie_use is not None:
            kwargs["cookie_use"] = cookie_use
        info = run_yt_dlp(url, cookies_from_browser, **kwargs)
        download_url, audio_url = choose_yt_dlp_media(info)
        if not download_url:
            return None, "no_downloadable_media", None
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
            cookies_unreadable=bool(cookie_use and cookie_use.unreadable),
        ), None, None
    except VideoSourceCancelled:
        raise
    except Exception as exc:
        reason = _resolver_failure_reason(exc)
        detail = _failure_detail(exc)
        logger.warning("yt-dlp could not resolve %s (%s): %s", url, reason, detail or "no output")
        return None, reason, detail or None


def resolve_with_yt_dlp(
    url: str,
    cookies_from_browser: str | None = None,
    *,
    cancellation_event: threading.Event | None = None,
) -> ResolvedVideo | None:
    resolved, _, _ = _resolve_with_yt_dlp_attempt(
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
        if not choose_miuistore_video_url(links):
            # The page can be a shell that fetches its result with a script
            # call; the fallback answered "no downloadable media" for every
            # link once the resolver moved to that (seen 2026-10-08).
            inner = _MIUISTORE_INNER_RESULT.search(page_html or "")
            if inner:
                page_html = fetch_text(
                    html.unescape(inner.group(1)),
                    allowed_host_suffixes=MIUISTORE_HOST_SUFFIXES + MIUISTORE_API_HOST_SUFFIXES,
                )
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
        reason = _resolver_failure_reason(exc)
        logger.warning("miuistore could not resolve the link (%s): %s", reason, _failure_detail(exc))
        return None, reason


def _cookie_store_unreadable(detail: str | None) -> bool:
    """yt-dlp could not open the browser's cookie store at all, as opposed to
    reading it and being refused by the site."""
    text = (detail or "").lower()
    if "cookie" not in text:
        return False
    return any(marker in text for marker in (
        "could not find", "could not copy", "cookies database", "cookie database",
        "operation not permitted", "permission denied", "failed to decrypt", "keyring",
    ))


DOUYIN_IMAGE_POST_MESSAGE = "图文作品没有可转写的音视频：这个抖音链接是图文作品（图片加配乐）。请换一个视频链接。"
DOUYIN_NO_MEDIA_MESSAGE = (
    "抖音没有给出可下载的视频：可能是图文作品（图文作品没有可转写的音视频）、视频已删除或设为私密。"
    "请在抖音里打开确认；能播放的话，把视频下载到本机后上传。"
)
GENERIC_UNRESOLVED_MESSAGE = "暂时无法自动解析这个视频链接，请上传视频文件"
_BILIBILI_LOGIN_REASONS = frozenset({"login_required", "forbidden", "premium_only"})


def _browser_label(browser: str) -> str:
    return browser.split(":", 1)[0].capitalize()


def _unreadable_login_message(browser: str, site: str) -> str:
    return (
        f"读不到 {_browser_label(browser)} 里的{site}登录：系统不允许 FluentFlow 读取这个浏览器的数据。"
        "可以在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」里允许启动 FluentFlow 的程序，"
        "或者先把视频下载到本机再上传。"
    )


def _bilibili_failure_message(reason: str, *, login_browser: str, cookie_use: CookieUse) -> str:
    login_usable = bool(login_browser) and not cookie_use.unreadable
    if reason in _BILIBILI_LOGIN_REASONS and not login_usable:
        if login_browser and cookie_use.unreadable:
            return _unreadable_login_message(login_browser, " B 站")
        return (
            "这个 B 站链接需要登录后才能下载。请在设置里选择已登录 B 站的浏览器（浏览器登录态），"
            "或改为上传本地视频。"
        )
    if reason in {"premium_only", "login_required"}:
        return (
            "这个 B 站链接是会员或付费内容，当前登录的账号没有观看权限。"
            "请在浏览器里换有权限的账号登录，或上传本地视频。"
        )
    if reason == "forbidden":
        return "B 站拒绝了这个链接的下载请求（403）。请稍后重试，或上传本地视频。"
    if reason in {"removed", "geo_blocked"}:
        return "这个 B 站链接的视频已删除、不存在或有地区限制。请在 B 站里打开确认，或上传本地视频。"
    if reason == "rate_limited":
        return "B 站暂时限制了请求（请求过于频繁）。请过几分钟再试，或上传本地视频。"
    return "暂时无法解析这个 B 站链接，B 站没有返回可下载的视频。请在 B 站里打开确认，或上传本地视频。"


_YOUTUBE_MESSAGES = {
    "bot_check": (
        "YouTube 要求验证「不是机器人」，拒绝了这台电脑对这个链接的请求。"
        "可以在设置里选择已登录 YouTube 的浏览器后重试，或上传视频文件。"
    ),
    "age_restricted": (
        "这个 YouTube 链接有年龄限制，需要已登录、满 18 岁的账号才能获取。"
        "请在设置里选择已登录 YouTube 的浏览器，或上传视频文件。"
    ),
    "private": "这个 YouTube 链接是私享视频，只有获得授权的账号能看。请上传视频文件。",
    "members_only": "这个 YouTube 链接是频道会员专享视频。请用有会员的账号在浏览器登录，或上传视频文件。",
    "geo_blocked": "这个 YouTube 链接在当前地区不可观看。请上传视频文件。",
    "removed": "这个 YouTube 链接的视频已删除或不可用。请确认链接，或上传视频文件。",
    "login_required": (
        "这个 YouTube 链接需要登录后才能获取。请在设置里选择已登录 YouTube 的浏览器，或上传视频文件。"
    ),
    "forbidden": (
        "视频下载失败：403，平台拒绝了这个链接的下载请求。"
        "请稍后重试、在设置里开启浏览器登录态（cookies），或上传视频文件"
    ),
}


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
    source_url = normalize_douyin_url(source_url)
    resolved = resolve_direct_video(source_url)
    if resolved:
        resolved.resolution_trace = [{"provider": "direct", "status": "selected"}]
        return resolved
    if not any(check(source_url) for check in (is_douyin_url, is_bilibili_url, is_youtube_url)):
        raise VideoSourceResolutionError(
            "目前只支持抖音、Bilibili、YouTube 或视频直链",
            [{"provider": "source-policy", "status": "failed", "reason": "unsupported_source"}],
        )
    if is_douyin_image_post_url(source_url):
        raise VideoSourceResolutionError(
            DOUYIN_IMAGE_POST_MESSAGE,
            [{"provider": "source-policy", "status": "failed", "reason": "image_post"}],
        )
    login_browser = (cookies_from_browser or os.environ.get("YT_DLP_COOKIES_FROM_BROWSER", "")).strip()
    cookie_use = CookieUse(sent=bool(_yt_dlp_cookies_args(cookies_from_browser)))
    resolved, failure_reason, failure_detail = _resolve_with_yt_dlp_attempt(
        source_url,
        cookies_from_browser,
        cookie_use=cookie_use,
        **_cancellation_kwargs(cancellation_event),
    )
    if resolved:
        resolved.resolution_trace = [*cookie_use.notes, {"provider": "yt-dlp", "status": "selected"}]
        resolved.cookies_unreadable = resolved.cookies_unreadable or cookie_use.unreadable
        return resolved
    if _cookie_store_unreadable(failure_detail):
        cookie_use.unreadable = True
        cookie_use.sent = False
    trace: list[dict[str, str]] = list(cookie_use.notes)
    failed: dict[str, str] = {"provider": "yt-dlp", "status": "failed", "reason": failure_reason or "unavailable"}
    if failure_detail:
        failed["detail"] = failure_detail
    trace.append(failed)
    reason = failure_reason or "unavailable"
    if is_bilibili_url(source_url):
        raise VideoSourceResolutionError(
            _bilibili_failure_message(reason, login_browser=login_browser, cookie_use=cookie_use),
            trace,
        )
    if not is_douyin_url(source_url):
        raise VideoSourceResolutionError(_YOUTUBE_MESSAGES.get(reason, GENERIC_UNRESOLVED_MESSAGE), trace)
    # What to say about the browser login if the fallback fails too. Said only
    # then, and only when the login is what went wrong: when the fallback works
    # the user gets the video and nothing to fix.
    login_hint: str | None = None
    if login_browser and cookie_use.unreadable:
        login_hint = _unreadable_login_message(login_browser, "抖音")
    elif login_browser and cookie_use.sent and reason == "fresh_cookies_required":
        # Cookies were sent and Douyin still wants fresh ones: the login in
        # the browser has gone stale, and only the user can renew it.
        login_hint = (
            f"抖音的登录信息过期了：在 {_browser_label(login_browser)} 里打开 douyin.com 登录一次，再重试这个链接。"
        )
    if not allow_miuistore:
        # The fallback runs by default: yt-dlp needs a fresh Douyin login and
        # fails without one. It sends the extracted Douyin URL to a third
        # party, and a caller can still switch it off.
        trace.append({"provider": "miuistore", "status": "skipped", "reason": "disabled_by_request"})
        raise VideoSourceResolutionError(login_hint or GENERIC_UNRESOLVED_MESSAGE, trace)
    # A browser login that did not work does not close the fallback. When the
    # login works, yt-dlp has already returned above and nothing goes to the
    # third party; `allow_miuistore` still switches it off entirely.
    resolved, fallback_reason = _resolve_with_miuistore_attempt(
        source_url, **_cancellation_kwargs(cancellation_event)
    )
    if resolved:
        trace.append({"provider": "miuistore", "status": "selected"})
        resolved.resolution_trace = trace
        resolved.cookies_unreadable = cookie_use.unreadable
        return resolved
    trace.append({"provider": "miuistore", "status": "failed", "reason": fallback_reason or "unavailable"})
    if fallback_reason == "no_downloadable_media":
        # The fallback reads the video without any login; when it finds no
        # video either, the post has none: an image post, deleted, or private.
        raise VideoSourceResolutionError(DOUYIN_NO_MEDIA_MESSAGE, trace)
    raise VideoSourceResolutionError(login_hint or GENERIC_UNRESOLVED_MESSAGE, trace)


def download_referer_for_url(url: str, fallback: str | None = None) -> str | None:
    """The Referer a media host expects, or None for hosts that expect none.

    A Douyin Referer used to go to every host that was not Bilibili's, so a
    YouTube or plain direct link carried a Douyin page as its origin.
    """
    if fallback:
        return fallback
    host = _url_host(url)
    if _host_matches_suffixes(host, BILIBILI_MEDIA_HOST_SUFFIXES) or is_bilibili_url(url):
        return "https://www.bilibili.com/"
    if _host_matches_suffixes(host, DOUYIN_MEDIA_HOST_SUFFIXES) or is_douyin_url(url):
        return "https://www.douyin.com/"
    return None


def _scratch_path(file_path: Path) -> Path:
    """A name of its own for one download attempt, next to the final file.

    Unique per attempt so two downloads of the same link can never write into
    each other's scratch file.
    """
    return file_path.with_name(f".{file_path.name}.{uuid.uuid4().hex[:12]}{PARTIAL_DOWNLOAD_SUFFIX}")


_target_locks: dict[str, threading.Lock] = {}
_target_locks_guard = threading.Lock()


@contextmanager
def _exclusive_target(key: Path, cancellation_event: threading.Event | None = None):
    """Hold the one download of ``key`` at a time.

    Two submissions of the same link resolve to the same file; without this
    they downloaded into it side by side. The second waits, then finds the
    first one's finished file and reuses it.
    """
    with _target_locks_guard:
        lock = _target_locks.setdefault(str(key), threading.Lock())
    while not lock.acquire(timeout=0.5):
        _raise_if_cancelled(cancellation_event)
    try:
        yield
    finally:
        lock.release()


def download_file(
    url: str,
    file_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    referer: str | None = None,
    allowed_host_suffixes: tuple[str, ...] = (),
    cancellation_event: threading.Event | None = None,
) -> int:
    """Download ``url`` to ``file_path``: the whole file or nothing.

    Written under a scratch name and renamed only once complete; every failure
    removes the scratch file, so nothing half-written ever sits at a name the
    next attempt would trust as "already downloaded".
    """
    _raise_if_cancelled(cancellation_event)
    effective_host_suffixes = allowed_host_suffixes or trusted_media_host_suffixes(url)
    headers = {"user-agent": "Mozilla/5.0 FluentFlow/1.0"}
    effective_referer = download_referer_for_url(url, referer)
    if effective_referer:
        headers["referer"] = effective_referer
    request = urllib.request.Request(url, headers=headers)
    max_bytes = max_video_bytes()
    downloaded = 0
    scratch = _scratch_path(file_path)
    try:
        with open_public_http_url(
            request,
            timeout=120,
            allowed_host_suffixes=effective_host_suffixes,
            redirect_to_any_public_host=effective_host_suffixes == DOUYIN_MEDIA_HOST_SUFFIXES,
        ) as response:
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > max_bytes:
                raise RuntimeError(link_too_large_message(content_length, max_bytes))
            on_progress and on_progress(VideoSourceProgress(
                stage="downloading",
                message="正在下载视频" if content_length else "正在下载视频，文件大小未知",
                percent=0 if content_length else None,
                loaded_bytes=0,
                total_bytes=content_length or None,
            ))
            with scratch.open("wb") as output:
                while True:
                    _raise_if_cancelled(cancellation_event)
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise RuntimeError(link_too_large_message(None, max_bytes))
                    output.write(chunk)
                    percent = min(99, round(downloaded / content_length * 100)) if content_length else None
                    on_progress and on_progress(VideoSourceProgress(
                        stage="downloading",
                        message="正在下载视频",
                        percent=percent,
                        loaded_bytes=downloaded,
                        total_bytes=content_length or None,
                    ))
            if content_length and downloaded < content_length:
                raise RuntimeError(f"视频下载中断：只收到 {downloaded} / {content_length} 字节")
            if downloaded == 0:
                raise RuntimeError("视频下载失败：服务器返回了空文件")
        scratch.replace(file_path)
    except urllib.error.HTTPError as exc:
        scratch.unlink(missing_ok=True)
        raise RuntimeError(f"视频下载失败：{exc.code}") from exc
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise
    size_bytes = downloaded
    on_progress and on_progress(VideoSourceProgress(
        stage="downloading",
        message="视频下载完成",
        percent=100,
        loaded_bytes=size_bytes,
        total_bytes=size_bytes,
    ))
    return size_bytes


def _yt_dlp_cookies_args(
    cookies_from_browser: str | None = None,
    url: str = "",
    *,
    enabled: bool = True,
) -> list[str]:
    if not enabled:
        return []
    value = (cookies_from_browser or os.environ.get("YT_DLP_COOKIES_FROM_BROWSER", "")).strip()
    if value:
        return ["--cookies-from-browser", browser_cookie_spec(value, url) if url else value]
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
    if _cookie_store_unreadable(text):
        return "cookie_store_unreadable"
    if "private video" in text:
        return "private"
    if "not a bot" in text:
        return "bot_check"
    if "confirm your age" in text or "age-restricted" in text or "age restricted" in text or "inappropriate for some users" in text:
        return "age_restricted"
    if "members-only" in text or "members only" in text or "join this channel" in text:
        return "members_only"
    if "premium member" in text or "大会员" in text or "only preview format" in text or "试看" in text:
        return "premium_only"
    if "available in your country" in text or "geo-restrict" in text or "geo restrict" in text or "地区限制" in text:
        return "geo_blocked"
    if "http error 403" in text or "forbidden" in text or "视频下载失败：403" in text:
        return "forbidden"
    if "http error 429" in text or "too many requests" in text:
        return "rate_limited"
    if "timed out" in text or "timeout" in text or "视频下载超时" in text:
        return "timeout"
    if "too large" in text or "文件过大" in text or "视频过大" in text or "larger than max-filesize" in text:
        return "too_large"
    if "没有可用字幕" in text or "no subtitles" in text or "no captions" in text:
        return "no_captions"
    if (
        "video unavailable" in text
        or "has been removed" in text
        or "may be deleted" in text
        or "does not exist" in text
        or "啥都木有" in text
    ):
        return "removed"
    if (
        "logged in" in text
        or "login required" in text
        or "sign in" in text
        or "--cookies-from-browser or --cookies" in text
        or "需要登录" in text
    ):
        return "login_required"
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
    cookie_use: CookieUse | None = None,
) -> int:
    """Fetch existing subtitles instead of the media, skipping STT entirely."""

    _raise_if_cancelled(cancellation_event)
    use = cookie_use if cookie_use is not None else CookieUse()
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

            def build(cookie_args: list[str], language: str = language, output_template: str = output_template) -> list[str]:
                return [
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
                    *cookie_args,
                    url,
                ]

            result = _run_yt_dlp_step(
                build,
                url=url,
                cookies_from_browser=cookies_from_browser,
                cookie_use=use,
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


_PROGRESS_PREFIX = "[fluentflow-progress]"
_PROGRESS_TEMPLATE = (
    "download:" + _PROGRESS_PREFIX
    + " %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s"
)
_MAX_FILESIZE_RE = re.compile(r"larger than max-filesize \((\d+) bytes > (\d+) bytes\)", re.I)


def _int_or_none(value: str) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _yt_dlp_progress_reporter(on_progress: ProgressCallback) -> Callable[[str], None]:
    """Turn yt-dlp's progress lines into progress reports.

    A video-plus-audio download fetches two files one after the other; the
    count restarting near zero marks the second, and the message says which
    one the percent belongs to.
    """
    state = {"part": 0, "last_percent": None}

    def handle(line: str) -> None:
        if not line.startswith(_PROGRESS_PREFIX):
            return
        fields = line[len(_PROGRESS_PREFIX):].split()
        if len(fields) < 3:
            return
        loaded = _int_or_none(fields[0])
        total = _int_or_none(fields[1]) or _int_or_none(fields[2])
        if loaded is None or not total:
            return
        part_percent = max(0.0, min(100.0, loaded / total * 100))
        last = state["last_percent"]
        if last is not None and part_percent + 50 < last:
            state["part"] += 1
        state["last_percent"] = part_percent
        on_progress(VideoSourceProgress(
            stage="downloading",
            message="正在下载视频" if state["part"] == 0 else "正在下载音频",
            percent=min(99, round(part_percent)),
            loaded_bytes=loaded,
            total_bytes=total,
        ))

    return handle


def _probe_duration_seconds(path: Path) -> float | None:
    """The media's duration by ffprobe, or None when it cannot be measured."""
    try:
        from backend.core.media_preflight import _duration_seconds, _probe_media

        return _duration_seconds(_probe_media(path))
    except Exception:  # noqa: BLE001 - an unmeasurable file is judged elsewhere
        return None


def bilibili_preview_message(downloaded_seconds: float | None, full_seconds: float | None) -> str:
    lengths = ""
    if downloaded_seconds and full_seconds:
        lengths = f"（拿到约 {downloaded_seconds / 60:.0f} 分钟，完整约 {full_seconds / 60:.0f} 分钟）"
    return (
        f"这个 B 站视频是会员/付费内容，只能拿到试看片段{lengths}，没有继续处理。"
        "请在设置里选择已登录有权限账号的浏览器后重试，或把完整视频下载到本机后上传。"
    )


def _is_preview_only(url: str, output: str, downloaded_seconds: float | None, full_seconds: float | None) -> bool:
    """Bilibili hands a paid or members-only video to anyone as a few-minute
    preview, and the download succeeds (measured 2026-10-08: 3 of 10 minutes)."""
    if not is_bilibili_url(url):
        return False
    if "only preview format" in output.lower():
        return True
    if downloaded_seconds and full_seconds and full_seconds - downloaded_seconds > 30:
        return downloaded_seconds < full_seconds * 0.8
    return False


def download_yt_dlp_media(
    url: str,
    file_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    duration_seconds: float | None = None,
    estimated_size_bytes: int | None = None,
    cookies_from_browser: str | None = None,
    cancellation_event: threading.Event | None = None,
    cookie_use: CookieUse | None = None,
) -> int:
    """Let yt-dlp download and merge the media itself.

    Bilibili's DASH streams sit on PCDN hosts with non-standard ports that our
    own downloader refuses, and YouTube serves picture and sound separately;
    yt-dlp handles both, merging with ffmpeg. The format is capped at 720p.
    """
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
    # Written in a scratch folder of its own and moved to the final name only
    # once yt-dlp has finished. A timeout, a non-zero exit or a cancel used to
    # leave a truncated file at the final name, which the next attempt then
    # read as "already downloaded" and transcribed as the video.
    scratch_dir = _scratch_path(file_path)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(scratch_dir / "media.%(ext)s")

    def build(cookie_args: list[str]) -> list[str]:
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--no-part",
            "--force-overwrites",
            "--max-filesize",
            str(max_bytes),
            "-f",
            yt_dlp_format_for(url),
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            *cookie_args,
            *_site_args(url),
        ]
        if on_progress is not None:
            args.extend(["--newline", "--progress", "--progress-template", _PROGRESS_TEMPLATE])
        else:
            args.append("--no-progress")
        args.append(url)
        return args

    def clear_scratch() -> None:
        for leftover in scratch_dir.iterdir():
            leftover.unlink(missing_ok=True)

    timeout = download_timeout_seconds(
        duration_seconds=duration_seconds,
        estimated_size_bytes=estimated_size_bytes,
    )
    try:
        try:
            result = _run_yt_dlp_step(
                build,
                url=url,
                cookies_from_browser=cookies_from_browser,
                cookie_use=cookie_use,
                timeout=timeout,
                cancellation_event=cancellation_event,
                on_output_line=_yt_dlp_progress_reporter(on_progress) if on_progress else None,
                before_retry=clear_scratch,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"视频下载超时：视频可能较大或当前网络较慢，已等待 {timeout} 秒。") from exc
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            logger.warning("yt-dlp download of %s failed (exit %s): %s", url, result.returncode, detail[-400:])
            raise RuntimeError(detail or f"yt-dlp 下载失败，退出码 {result.returncode}")
        too_large = _MAX_FILESIZE_RE.search(output)
        candidates = sorted(
            (item for item in scratch_dir.iterdir() if item.is_file() and item.stat().st_size > 0),
            key=lambda item: (item.suffix.lower() != ".mp4", -item.stat().st_size),
        )
        if not candidates:
            if too_large:
                raise RuntimeError(link_too_large_message(int(too_large.group(1)), max_bytes))
            raise RuntimeError("yt-dlp 没有写出视频文件")
        produced = candidates[0]
        size_bytes = produced.stat().st_size
        if size_bytes > max_bytes:
            raise RuntimeError(link_too_large_message(size_bytes, max_bytes))
        if is_bilibili_url(url):
            downloaded_seconds = _probe_duration_seconds(produced)
            if _is_preview_only(url, output, downloaded_seconds, duration_seconds):
                raise RuntimeError(bilibili_preview_message(downloaded_seconds, duration_seconds))
        produced.replace(file_path)
    finally:
        # Every path, cancellation included: nothing may be left at a name
        # the next attempt would trust.
        shutil.rmtree(scratch_dir, ignore_errors=True)
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


def _reusable_download(file_path: Path) -> bool:
    """Whether a file already at ``file_path`` is a whole download to reuse.

    A download that was cut off, or a file damaged since, is removed so it is
    fetched again instead of being transcribed as the video.
    """
    try:
        size = file_path.stat().st_size
    except FileNotFoundError:
        return False
    if size <= 0:
        file_path.unlink(missing_ok=True)
        return False
    if file_path.suffix.lower() == ".srt":
        return True
    from backend.core.media_preflight import MediaPreflightError, preflight_media_file

    try:
        preflight_media_file(file_path)
    except MediaPreflightError as exc:
        if exc.code == "media_preflight_unavailable":
            # No ffprobe on this machine: nothing to check with. The download
            # itself only ever renames a finished file to this name.
            return True
        logger.warning("discarding unusable earlier download %s (%s)", file_path, exc.code)
        file_path.unlink(missing_ok=True)
        return False
    except Exception:  # noqa: BLE001 - an unreadable file is not reused
        logger.warning("discarding unreadable earlier download %s", file_path, exc_info=True)
        file_path.unlink(missing_ok=True)
        return False
    return True


def _check_limits_before_download(
    resolved: ResolvedVideo,
    *,
    duration_limit_seconds: float | None,
) -> None:
    """Refuse a link whose video is known to be over a limit before fetching it.

    The size and length come from the platform; finding out after a long
    download, or after waiting in the transcription queue, wasted both.
    """
    limit_bytes = max_video_bytes()
    if resolved.estimated_size_bytes and resolved.estimated_size_bytes > limit_bytes:
        raise RuntimeError(link_too_large_message(resolved.estimated_size_bytes, limit_bytes, estimated=True))
    if (
        duration_limit_seconds
        and resolved.duration_seconds
        and resolved.duration_seconds > duration_limit_seconds
    ):
        raise RuntimeError(link_too_long_message(resolved.duration_seconds, duration_limit_seconds))


_DOUYIN_DOWNLOAD_FAILED = "抖音视频解析成功，但下载没有成功{fallback}。请稍后重试，或把视频下载到本机后上传。"


def _download_douyin_via_yt_dlp(
    resolved: ResolvedVideo,
    file_path: Path,
    on_progress: ProgressCallback | None,
    *,
    cookies_from_browser: str | None,
    cookie_use: CookieUse,
    allow_miuistore: bool,
    cancellation_event: threading.Event | None,
) -> int:
    """Download a Douyin video yt-dlp resolved, with the resolver's retry
    policy, falling back to the third-party resolver if it still fails."""
    attempts = 0
    while True:
        try:
            return download_yt_dlp_media(
                resolved.source_url,
                file_path,
                on_progress,
                duration_seconds=resolved.duration_seconds,
                estimated_size_bytes=resolved.estimated_size_bytes,
                cookies_from_browser=cookies_from_browser,
                cookie_use=cookie_use,
                **_cancellation_kwargs(cancellation_event),
            )
        except VideoSourceCancelled:
            raise
        except Exception as exc:
            reason = _resolver_failure_reason(exc)
            if reason == "too_large" or "链接视频过大" in str(exc):
                raise
            if _should_retry(reason, cookie_use) and attempts < _RESOLVER_RETRIES:
                attempts += 1
                _pause_before_retry(attempts, cancellation_event)
                continue
            failure = {
                "provider": "yt-dlp-download",
                "status": "failed",
                "reason": reason,
                "detail": _failure_detail(exc),
            }
            break
    trace = [*(resolved.resolution_trace or []), failure]
    if not allow_miuistore:
        trace.append({"provider": "miuistore", "status": "skipped", "reason": "disabled_by_request"})
        raise VideoSourceResolutionError(_DOUYIN_DOWNLOAD_FAILED.format(fallback=""), trace)
    fallback, fallback_reason = _resolve_with_miuistore_attempt(
        resolved.source_url, **_cancellation_kwargs(cancellation_event)
    )
    if fallback is None:
        trace.append({"provider": "miuistore", "status": "failed", "reason": fallback_reason or "unavailable"})
        if fallback_reason == "no_downloadable_media":
            raise VideoSourceResolutionError(DOUYIN_NO_MEDIA_MESSAGE, trace)
        raise VideoSourceResolutionError(
            _DOUYIN_DOWNLOAD_FAILED.format(fallback="，第三方解析也没有拿到"), trace
        )
    trace.append({"provider": "miuistore", "status": "selected"})
    resolved.resolution_trace = trace
    try:
        return download_file(
            fallback.download_url,
            file_path,
            on_progress,
            allowed_host_suffixes=DOUYIN_MEDIA_HOST_SUFFIXES,
            **_cancellation_kwargs(cancellation_event),
        )
    except VideoSourceCancelled:
        raise
    except Exception as exc:
        trace[-1] = {"provider": "miuistore", "status": "failed", "reason": _resolver_failure_reason(exc),
                     "detail": _failure_detail(exc)}
        if "链接视频过大" in str(exc):
            raise
        raise VideoSourceResolutionError(
            _DOUYIN_DOWNLOAD_FAILED.format(fallback="，第三方解析的下载也失败了"), trace
        ) from exc


def _download_media(
    resolved: ResolvedVideo,
    file_path: Path,
    on_progress: ProgressCallback | None,
    *,
    cookies_from_browser: str | None,
    cookie_use: CookieUse,
    allow_miuistore: bool,
    cancellation_event: threading.Event | None,
) -> int:
    if resolved.provider == "yt-dlp" and is_douyin_url(resolved.source_url):
        return _download_douyin_via_yt_dlp(
            resolved,
            file_path,
            on_progress,
            cookies_from_browser=cookies_from_browser,
            cookie_use=cookie_use,
            allow_miuistore=allow_miuistore,
            cancellation_event=cancellation_event,
        )
    if resolved.provider == "yt-dlp":
        return download_yt_dlp_media(
            resolved.source_url,
            file_path,
            on_progress,
            duration_seconds=resolved.duration_seconds,
            estimated_size_bytes=resolved.estimated_size_bytes,
            cookies_from_browser=cookies_from_browser,
            cookie_use=cookie_use,
            **_cancellation_kwargs(cancellation_event),
        )
    if resolved.provider == "miuistore":
        return download_file(
            resolved.download_url,
            file_path,
            on_progress,
            allowed_host_suffixes=DOUYIN_MEDIA_HOST_SUFFIXES,
            **_cancellation_kwargs(cancellation_event),
        )
    return download_file(
        resolved.download_url,
        file_path,
        on_progress,
        referer=resolved.referer,
        **_cancellation_kwargs(cancellation_event),
    )


def download_video_source(
    input_text: str,
    *,
    title: str | None = None,
    video_dir: Path,
    on_progress: ProgressCallback | None = None,
    cookies_from_browser: str | None = None,
    allow_miuistore: bool = True,
    duration_limit_seconds: float | None = None,
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
    cookie_use = CookieUse(unreadable=resolved.cookies_unreadable)
    caption_source = None
    if resolved.provider == "yt-dlp":
        caption_source = captions_first_provider(
            resolved.source_url, None if cookie_use.unreadable else cookies_from_browser
        )
        if caption_source == "bilibili" and cookie_use.unreadable:
            caption_source = None
    media_type = "transcript" if caption_source else "video"
    extension = ".srt" if media_type == "transcript" else ".mp4"
    video_id, raw_title, display_title, filename = resolve_filename(resolved, title, extension=extension)
    file_path = video_dir / filename
    caption_failure_reason: str | None = None
    downloaded_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _exclusive_target(video_dir / video_id, cancellation_event):
        if _reusable_download(file_path):
            size_bytes = file_path.stat().st_size
            on_progress and on_progress(VideoSourceProgress(
                stage="downloading",
                message="本地已有视频文件",
                percent=100,
                loaded_bytes=size_bytes,
                total_bytes=size_bytes,
            ))
        else:
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
                        cookie_use=cookie_use,
                        **_cancellation_kwargs(cancellation_event),
                    )
                except VideoSourceCancelled:
                    raise
                except Exception as exc:
                    caption_failure_reason = video_source_failure_reason(exc)
                    media_type = "video"
                    video_id, raw_title, display_title, filename = resolve_filename(resolved, title)
                    file_path = video_dir / filename

            if media_type == "video" and _reusable_download(file_path):
                size_bytes = file_path.stat().st_size
            elif media_type == "video":
                caption_label = CAPTION_UNAVAILABLE_LABELS.get(caption_source or "", "字幕")
                _check_limits_before_download(resolved, duration_limit_seconds=duration_limit_seconds)
                try:
                    size_bytes = _download_media(
                        resolved,
                        file_path,
                        on_progress,
                        cookies_from_browser=cookies_from_browser,
                        cookie_use=cookie_use,
                        allow_miuistore=allow_miuistore,
                        cancellation_event=cancellation_event,
                    )
                except VideoSourceCancelled:
                    raise
                except VideoSourceResolutionError as media_exc:
                    if caption_failure_reason:
                        raise VideoSourceResolutionError(
                            f"{caption_label}不可用，且原视频下载失败：{media_exc}", media_exc.resolution_trace
                        ) from media_exc
                    raise
                except Exception as media_exc:
                    if caption_failure_reason:
                        raise RuntimeError(
                            f"{caption_label}不可用，且原视频下载失败：{media_exc}"
                        ) from media_exc
                    raise

    _raise_if_cancelled(cancellation_event)
    resolution_trace = list(resolved.resolution_trace or [])
    for note in cookie_use.notes:
        if note not in resolution_trace:
            resolution_trace.insert(0, note)
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
        "resolution_trace": resolution_trace,
    }
    metadata_path = write_json_metadata(file_path, metadata)
    write_source_info(video_dir, display_title, resolved.source_url)
    return SavedVideoSource(ok=True, metadata_path=str(metadata_path), **metadata)
