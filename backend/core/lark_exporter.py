"""Lark / Feishu exporter: create a document and write Markdown content as blocks."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dotenv import load_dotenv
from backend.core.feishu_markdown import normalize_markdown_for_feishu

logger = logging.getLogger(__name__)

# 国际版 Lark 默认域名；国内飞书企业应用请设环境变量 LARK_OPEN_BASE_URL=https://open.feishu.cn
DEFAULT_BASE_URL = "https://open.larksuite.com"

# Feishu docx block types
# Official SDK models expose heading1~heading9, then bullet/ordered/code.
# That means:
#   text=2, heading1~heading9=3..11, bullet=12, ordered=13, code=14, divider=22.
_BT_TEXT = 2
_BT_H1 = 3
_BT_H2 = 4
_BT_H3 = 5
_BT_H4 = 6
_BT_H5 = 7
_BT_H6 = 8
_BT_BULLET = 12
_BT_ORDERED = 13
_BT_CODE = 14
_BT_DIVIDER = 22
_BT_IMAGE = 27

_HEADING_FIELD = {3: "heading1", 4: "heading2", 5: "heading3",
                  6: "heading4", 7: "heading5", 8: "heading6"}

_MAX_BLOCKS_PER_BATCH = 50


# ---------------------------------------------------------------------------
# Markdown → Feishu block dicts
# ---------------------------------------------------------------------------

def _parse_inline(text: str) -> List[dict]:
    """Parse **bold** into a list of Feishu TextElement dicts."""
    parts = text.split("**")
    elements: List[dict] = []
    for idx, part in enumerate(parts):
        if not part:
            continue
        run: dict = {"content": part}
        if idx % 2 == 1:
            run["text_element_style"] = {"bold": True}
        elements.append({"text_run": run})
    return elements or [{"text_run": {"content": text}}]


def _text_body(text: str) -> dict:
    return {"elements": _parse_inline(text)}


def _text_block(text: str) -> dict:
    return {"block_type": _BT_TEXT, "text": _text_body(text)}


def _heading_block(text: str, level: int) -> dict:
    bt = _BT_H1 + level - 1
    field = _HEADING_FIELD.get(bt, "heading1")
    return {"block_type": bt, field: _text_body(text)}


def _bullet_block(text: str) -> dict:
    return {"block_type": _BT_BULLET, "bullet": _text_body(text)}


def _ordered_block(text: str) -> dict:
    return {"block_type": _BT_ORDERED, "ordered": _text_body(text)}


def _code_block(code: str) -> dict:
    return {"block_type": _BT_CODE, "code": _text_body(code)}


def _image_block() -> dict:
    return {"block_type": _BT_IMAGE, "image": {}}


def _image_fallback_block(alt: str, src: str) -> dict:
    parsed = urlparse(src)
    label = alt.strip() or Path(parsed.path).name or src
    return _text_block(f"图片：{label}")


_RE_ORDERED = re.compile(r"^\d+[.）]\s+(.+)")
_RE_MD_IMAGE = re.compile(r"^!\[(.*?)\]\((.*?)\)$")
_RE_MD_TABLE_ALIGN = re.compile(
    r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?$"
)


def _looks_like_markdown_table(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    head = lines[index].strip()
    align = lines[index + 1].strip()
    if "|" not in head or "|" not in align:
        return False
    return bool(_RE_MD_TABLE_ALIGN.match(align))


def markdown_contains_table(markdown: str) -> bool:
    lines = markdown.split("\n")
    return any(_looks_like_markdown_table(lines, i) for i in range(len(lines) - 1))


def _markdown_to_feishu_blocks_with_image_refs(
    markdown: str,
    *,
    image_resolver: Optional[Callable[[str], Optional[Path]]] = None,
) -> tuple[List[dict], List[dict[str, Any]]]:
    """Convert Markdown to flat blocks and track resolvable image blocks."""
    blocks: List[dict] = []
    image_refs: List[dict[str, Any]] = []
    markdown = normalize_markdown_for_feishu(markdown)
    lines = markdown.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code block
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1  # skip closing ```
            blocks.append(_code_block("\n".join(code_lines)))
            continue

        # headings (check longer prefixes first)
        if stripped.startswith("###### "):
            blocks.append(_heading_block(stripped[7:], 6))
        elif stripped.startswith("##### "):
            blocks.append(_heading_block(stripped[6:], 5))
        elif stripped.startswith("#### "):
            blocks.append(_heading_block(stripped[5:], 4))
        elif stripped.startswith("### "):
            blocks.append(_heading_block(stripped[4:], 3))
        elif stripped.startswith("## "):
            blocks.append(_heading_block(stripped[3:], 2))
        elif stripped.startswith("# "):
            blocks.append(_heading_block(stripped[2:], 1))

        # unordered list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append(_bullet_block(stripped[2:]))

        # ordered list
        elif (m := _RE_ORDERED.match(stripped)):
            blocks.append(_ordered_block(m.group(1)))

        # blockquote → render as italic paragraph with "❝" prefix
        elif stripped.startswith("> "):
            blocks.append(_text_block("❝ " + stripped[2:]))

        # divider
        elif stripped in ("---", "***", "___"):
            blocks.append({"block_type": _BT_DIVIDER, "divider": {}})

        # selected screenshot evidence
        elif (m := _RE_MD_IMAGE.match(stripped)):
            alt = m.group(1) or ""
            src = m.group(2) or ""
            image_path = image_resolver(src) if image_resolver else None
            if image_path and image_path.is_file():
                blocks.append(_image_block())
                image_refs.append({
                    "block_index": len(blocks) - 1,
                    "alt": alt,
                    "src": src,
                    "path": image_path,
                })
            else:
                blocks.append(_image_fallback_block(alt, src))

        # regular paragraph
        else:
            blocks.append(_text_block(stripped))

        i += 1

    return blocks, image_refs


def markdown_to_feishu_blocks(markdown: str) -> List[dict]:
    """Convert a Markdown string to a flat list of Feishu block dicts."""
    blocks, _ = _markdown_to_feishu_blocks_with_image_refs(markdown)
    return blocks


# ---------------------------------------------------------------------------
# Low-level Feishu HTTP helpers (avoids SDK model-version issues for blocks)
# ---------------------------------------------------------------------------

def _get_tenant_token(app_id: str, app_secret: str, base_url: str, timeout: int = 15) -> str:
    url = f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu tenant token error: {data}")
    return data["tenant_access_token"]


def _public_docx_url(doc_id: str, base_url: str) -> str:
    """Browser URL for the docx; depends on tenant (China vs international)."""
    b = (base_url or "").lower()
    if "open.feishu.cn" in b or "feishu.cn" in b:
        return f"https://feishu.cn/docx/{doc_id}"
    return f"https://larksuite.com/docx/{doc_id}"


def _public_wiki_url(node_token: str, base_url: str) -> str:
    """Browser URL for a document created inside a Feishu knowledge library."""
    b = (base_url or "").lower()
    if "open.feishu.cn" in b or "feishu.cn" in b:
        return f"https://feishu.cn/wiki/{node_token}"
    return f"https://larksuite.com/wiki/{node_token}"


def _wiki_request_with_token(
    token: str,
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu wiki HTTP {exc.code}: {raw[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Feishu wiki request failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Feishu wiki returned invalid JSON") from exc
    if payload.get("code") != 0:
        raise RuntimeError(f"Feishu wiki error: {payload}")
    return payload.get("data") or {}


def _find_my_library_space_id(token: str, base_url: str, timeout: int) -> str:
    """Find the current user's single personal "My Library" wiki space."""
    page_token: str | None = None
    for _ in range(100):
        query = {"page_size": "50"}
        if page_token:
            query["page_token"] = page_token
        data = _wiki_request_with_token(
            token,
            base_url,
            f"/open-apis/wiki/v2/spaces?{urlencode(query)}",
            timeout=timeout,
        )
        items = data.get("items") or data.get("spaces") or []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict) or item.get("space_type") != "my_library":
                    continue
                space_id = str(item.get("space_id") or "").strip()
                if space_id:
                    return space_id
        if not data.get("has_more"):
            break
        next_page_token = str(data.get("page_token") or "").strip()
        if not next_page_token or next_page_token == page_token:
            break
        page_token = next_page_token
    raise RuntimeError("Feishu wiki personal library was not found for this account")


def _create_doc_in_my_library(
    token: str,
    base_url: str,
    title: str,
    timeout: int,
) -> dict[str, str]:
    """Create a docx node at the root of the user's personal knowledge library."""
    space_id = _find_my_library_space_id(token, base_url, timeout)
    data = _wiki_request_with_token(
        token,
        base_url,
        f"/open-apis/wiki/v2/spaces/{quote(space_id, safe='')}/nodes",
        method="POST",
        body={"obj_type": "docx", "node_type": "origin", "title": title},
        timeout=timeout,
    )
    node = data.get("node") if isinstance(data.get("node"), dict) else data
    if not isinstance(node, dict):
        raise RuntimeError(f"Feishu wiki create-node response is invalid: {data}")
    doc_token = str(node.get("obj_token") or node.get("document_id") or "").strip()
    node_token = str(node.get("node_token") or "").strip()
    if not doc_token or not node_token:
        raise RuntimeError(f"Feishu wiki create-node response is missing document or node token: {data}")
    return {"space_id": space_id, "node_token": node_token, "doc_token": doc_token}


def _create_empty_doc_with_token(
    token: str,
    base_url: str,
    title: str,
    folder_token: Optional[str],
    timeout: int,
) -> str:
    url = f"{base_url.rstrip('/')}/open-apis/docx/v1/documents"
    body: dict[str, Any] = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Feishu create-doc HTTP {exc.code}: {raw[:1200]}") from exc
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu create-doc error: {data}")
    payload = data.get("data") or {}
    doc = payload.get("document") if isinstance(payload.get("document"), dict) else payload
    doc_id = doc.get("document_id") or doc.get("doc_token") or doc.get("token")
    if not doc_id:
        raise RuntimeError(f"Feishu create-doc response missing document_id: {data}")
    return str(doc_id)


def _convert_markdown_via_openapi(
    token: str, base_url: str, markdown: str, timeout: int
) -> dict:
    """POST /docx/v1/documents/blocks/convert — returns official block tree."""
    url = f"{base_url.rstrip('/')}/open-apis/docx/v1/documents/blocks/convert"
    body = json.dumps(
        {"content_type": "markdown", "content": markdown or ""},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            err = json.loads(raw)
        except Exception:
            raise RuntimeError(f"Feishu convert HTTP {e.code}: {raw[:1200]}") from e
        raise RuntimeError(f"Feishu convert HTTP {e.code}: {err}") from e
    if data.get("code") != 0:
        raise RuntimeError(
            f"Feishu Markdown→blocks 失败（请检查应用是否开通 docx:document.block:convert 等权限）: {data}"
        )
    return data.get("data") or {}


def _sanitize_converted_block_for_descendant(b: dict) -> dict:
    """Prepare a convert API block for create-descendant API."""
    out = {k: v for k, v in b.items() if k != "parent_id"}
    tbl = out.get("table")
    if isinstance(tbl, dict):
        # `cells` and `merge_info` in convert output are server-generated/read-only.
        # The create-descendant API uses block `children` relationships instead.
        tbl.pop("cells", None)
        prop = tbl.get("property")
        if isinstance(prop, dict):
            prop.pop("merge_info", None)
    return out


def _convert_data_to_descendant_payload(data: dict) -> tuple[List[str], List[dict]]:
    """Use convert response as the official create-descendant payload."""
    blocks = data.get("blocks") or []
    if not blocks:
        return [], []
    by_id: Dict[str, dict] = {}
    for b in blocks:
        bid = b.get("block_id")
        if bid:
            by_id[str(bid)] = b
    first_ids = data.get("first_level_block_ids") or []
    if not first_ids:
        if all(not (x.get("children") or []) for x in blocks):
            descendants = [
                _sanitize_converted_block_for_descendant(x)
                for x in blocks
                if x.get("block_id")
            ]
            return [str(x["block_id"]) for x in descendants], descendants
        raise ValueError("convert response missing first_level_block_ids")
    children_id = [str(bid) for bid in first_ids if str(bid) in by_id]
    descendants = [
        _sanitize_converted_block_for_descendant(b)
        for b in blocks
        if b.get("block_id")
    ]
    return children_id, descendants


def _post_block_children(
    token: str,
    base_url: str,
    doc_id: str,
    blocks: List[dict],
    timeout: int = 15,
    *,
    document_revision_id: int = -1,
) -> dict:
    # -1 = 操作文档最新版本（飞书文档；首批写入建议显式带上）
    qs: Dict[str, Union[int, str]] = {"document_revision_id": document_revision_id}
    query = "?" + urlencode(qs)
    url = (
        f"{base_url.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/"
        f"blocks/{doc_id}/children{query}"
    )
    body = json.dumps({"children": blocks}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            err = json.loads(raw)
        except Exception:
            raise RuntimeError(f"Feishu 写入块 HTTP {e.code}: {raw[:1200]}") from e
        raise RuntimeError(f"Feishu 写入块 HTTP {e.code}: {err}") from e


def _post_block_descendants(
    token: str,
    base_url: str,
    doc_id: str,
    children_id: List[str],
    descendants: List[dict],
    timeout: int = 15,
    *,
    document_revision_id: int = -1,
) -> dict:
    qs: Dict[str, Union[int, str]] = {"document_revision_id": document_revision_id}
    query = "?" + urlencode(qs)
    url = (
        f"{base_url.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/"
        f"blocks/{doc_id}/descendant{query}"
    )
    body = json.dumps(
        {"children_id": children_id, "index": -1, "descendants": descendants},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            err = json.loads(raw)
        except Exception:
            raise RuntimeError(f"Feishu 写入嵌套块 HTTP {e.code}: {raw[:1200]}") from e
        raise RuntimeError(f"Feishu 写入嵌套块 HTTP {e.code}: {err}") from e


def _created_block_ids(result: dict[str, Any], expected_count: int) -> List[Optional[str]]:
    data = result.get("data") if isinstance(result, dict) else {}
    if not isinstance(data, dict):
        return [None] * expected_count
    raw_children = data.get("children") or data.get("blocks") or data.get("block_ids") or []
    ids: List[Optional[str]] = []
    if isinstance(raw_children, list):
        for item in raw_children:
            if isinstance(item, dict):
                ids.append(str(item.get("block_id") or item.get("id") or "") or None)
            elif item:
                ids.append(str(item))
    if len(ids) < expected_count:
        ids.extend([None] * (expected_count - len(ids)))
    return ids[:expected_count]


def _multipart_form_data(
    fields: dict[str, str],
    *,
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----FluentFlow{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _upload_docx_image(
    token: str,
    base_url: str,
    image_path: Path,
    image_block_id: str,
    timeout: int,
) -> str:
    url = f"{base_url.rstrip('/')}/open-apis/drive/v1/medias/upload_all"
    file_bytes = image_path.read_bytes()
    content_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    body, multipart_type = _multipart_form_data(
        {
            "file_name": image_path.name,
            "parent_type": "docx_image",
            "parent_node": image_block_id,
            "size": str(len(file_bytes)),
        },
        file_field="file",
        file_name=image_path.name,
        file_bytes=file_bytes,
        content_type=content_type,
    )
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": multipart_type,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        raise RuntimeError(f"Feishu 图片上传 HTTP {e.code}: {raw[:1200]}") from e
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu 图片上传失败: {data}")
    payload = data.get("data") or {}
    image_token = payload.get("file_token") or payload.get("token")
    if not image_token:
        raise RuntimeError(f"Feishu 图片上传未返回 file_token: {data}")
    return str(image_token)


def _replace_docx_image(
    token: str,
    base_url: str,
    doc_id: str,
    image_block_id: str,
    image_token: str,
    timeout: int,
) -> None:
    url = (
        f"{base_url.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/"
        f"blocks/{image_block_id}/replace_image"
    )
    body = json.dumps({"token": image_token}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        raise RuntimeError(f"Feishu 图片替换 HTTP {e.code}: {raw[:1200]}") from e
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu 图片替换失败: {data}")


def _resolve_markdown_artifact_image(
    src: str,
    *,
    task_id: Optional[str],
    artifact_root: Optional[Path],
) -> Optional[Path]:
    if not task_id or artifact_root is None:
        return None
    if "/" in task_id or "\\" in task_id or ".." in task_id or Path(task_id).is_absolute():
        return None
    parsed = urlparse(src)
    path = parsed.path or src
    expected = f"/jobs/{task_id}/artifacts/frame"
    if path != expected:
        return None
    frame_file = (parse_qs(parsed.query or "").get("file") or [""])[0].strip()
    if not frame_file or "/" in frame_file or "\\" in frame_file or ".." in frame_file:
        return None
    root = artifact_root.expanduser().resolve()
    task_root = (root / task_id).resolve()
    try:
        task_root.relative_to(root)
    except ValueError:
        return None
    frames_root = task_root / "frames"
    candidate = (frames_root / Path(frame_file).name).resolve()
    try:
        candidate.relative_to(frames_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


# ---------------------------------------------------------------------------
# LarkExporter
# ---------------------------------------------------------------------------

class LarkExporter:
    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        user_access_token: Optional[str] = None,
        timeout: int = 15,
        dry_run: bool = False,
    ) -> None:
        load_dotenv()
        self.app_id = app_id or os.environ.get("LARK_APP_ID")
        self.app_secret = app_secret or os.environ.get("LARK_APP_SECRET")
        self.user_access_token = (user_access_token or "").strip()
        env_base = (os.environ.get("LARK_OPEN_BASE_URL") or "").strip()
        if env_base:
            self.base_url = env_base.rstrip("/")
        else:
            self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.dry_run = bool(dry_run)

    def _require_creds(self) -> None:
        if self.user_access_token:
            return
        if not (self.app_id and self.app_secret):
            raise ValueError(
                "Lark credentials not set: provide app_id/app_secret or "
                "set LARK_APP_ID and LARK_APP_SECRET"
            )

    def _build_sdk_client(self):
        import lark_oapi as lark
        return lark.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .domain(self.base_url) \
            .build()

    def _create_empty_doc(self, title: str, folder_token: Optional[str]) -> str:
        """Create an empty document via SDK; return document_id."""
        from lark_oapi.api.docx.v1.model import (
            CreateDocumentRequest,
            CreateDocumentRequestBody,
        )
        client = self._build_sdk_client()
        rb = CreateDocumentRequestBody.builder().title(title)
        if folder_token:
            rb = rb.folder_token(folder_token)
        request = CreateDocumentRequest.builder().request_body(rb.build()).build()

        response = client.docx.v1.document.create(request)
        if not response.success():
            raise RuntimeError(
                f"Lark create-doc error: code={response.code}, msg={response.msg}"
            )
        doc = response.data.document
        if not doc or not doc.document_id:
            raise RuntimeError("Lark returned no document_id")
        return doc.document_id

    def _write_converted_descendants(
        self,
        doc_id: str,
        token: str,
        children_id: List[str],
        descendants: List[dict],
    ) -> None:
        """Append official converted block trees through create-descendant API."""
        if not children_id or not descendants:
            raise RuntimeError("没有可写入的文档块（内容为空）")
        if len(descendants) > 1000:
            raise RuntimeError("Feishu 官方转换结果超过单次嵌套块写入上限（1000 块）")
        t = max(self.timeout, 90)
        result = _post_block_descendants(
            token,
            self.base_url,
            doc_id,
            children_id,
            descendants,
            t,
            document_revision_id=-1,
        )
        if result.get("code") != 0:
            raise RuntimeError(
                f"Feishu 写入嵌套块失败: code={result.get('code')} msg={result.get('msg')} body={result}"
            )

    def _write_flat_blocks_batched(self, doc_id: str, token: str, blocks: List[dict]) -> List[Optional[str]]:
        """Legacy flat blocks from markdown_to_feishu_blocks; fail on API error."""
        if not blocks:
            return []
        t = max(self.timeout, 90)
        rev: Optional[int] = None
        created_ids: List[Optional[str]] = []
        for start in range(0, len(blocks), _MAX_BLOCKS_PER_BATCH):
            batch = blocks[start : start + _MAX_BLOCKS_PER_BATCH]
            doc_rev = -1 if rev is None else rev
            result = _post_block_children(
                token,
                self.base_url,
                doc_id,
                batch,
                t,
                document_revision_id=doc_rev,
            )
            if result.get("code") != 0:
                raise RuntimeError(
                    f"Feishu 写入块失败: code={result.get('code')} msg={result.get('msg')} body={result}"
                )
            created_ids.extend(_created_block_ids(result, len(batch)))
            data = result.get("data") or {}
            r = data.get("document_revision_id")
            if r is not None:
                rev = int(r)
        return created_ids

    def _upload_flat_image_refs(
        self,
        doc_id: str,
        token: str,
        image_refs: List[dict[str, Any]],
        created_block_ids: List[Optional[str]],
    ) -> tuple[int, list[str]]:
        uploaded = 0
        errors: list[str] = []
        t = max(self.timeout, 90)
        for ref in image_refs:
            block_index = int(ref.get("block_index") or -1)
            block_id = created_block_ids[block_index] if 0 <= block_index < len(created_block_ids) else None
            image_path = ref.get("path")
            if not block_id or not isinstance(image_path, Path) or not image_path.is_file():
                errors.append(str(ref.get("src") or ref.get("alt") or "image"))
                continue
            try:
                image_token = _upload_docx_image(token, self.base_url, image_path, block_id, t)
                _replace_docx_image(token, self.base_url, doc_id, block_id, image_token, t)
                uploaded += 1
            except Exception as exc:
                logger.warning("Feishu image upload skipped for %s: %s", image_path, exc)
                errors.append(str(ref.get("src") or image_path.name))
        return uploaded, errors

    def create_doc_markdown(
        self,
        title: str,
        markdown: str,
        folder_token: Optional[str] = None,
        *,
        task_id: Optional[str] = None,
        artifact_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Create a Feishu document and populate it with parsed Markdown content."""
        self._require_creds()
        export_markdown = normalize_markdown_for_feishu(markdown)

        if self.dry_run:
            blocks = markdown_to_feishu_blocks(export_markdown)
            logger.info(
                "Dry-run: would create doc '%s' (%d blocks) in folder '%s'",
                title, len(blocks), folder_token,
            )
            return {"ok": True, "dry_run": True, "title": title, "block_count": len(blocks)}

        if self.user_access_token:
            token = self.user_access_token
            if folder_token:
                doc_id = _create_empty_doc_with_token(token, self.base_url, title, folder_token, self.timeout)
                export_destination = "drive_folder"
                wiki_location: dict[str, str] = {}
            else:
                wiki_location = _create_doc_in_my_library(token, self.base_url, title, self.timeout)
                doc_id = wiki_location["doc_token"]
                export_destination = "my_library"
            auth_mode = "user_oauth"
        else:
            doc_id = self._create_empty_doc(title, folder_token)
            token = _get_tenant_token(
                self.app_id, self.app_secret, self.base_url, self.timeout
            )
            auth_mode = "tenant_token"
            export_destination = "drive_folder" if folder_token else "drive_root"
            wiki_location = {}
        conv_timeout = max(self.timeout, 90)
        disable_convert = (
            os.environ.get("FLUENTFLOW_LARK_DISABLE_OPENAPI_CONVERT", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        wants_convert = not disable_convert
        used_convert = False
        block_count = 0
        converted_children_id: List[str] = []
        converted_descendants: List[dict] = []
        image_resolver = (
            lambda src: _resolve_markdown_artifact_image(src, task_id=task_id, artifact_root=artifact_root)
        )
        image_upload_count = 0
        image_upload_errors: list[str] = []
        if wants_convert:
            try:
                conv_data = _convert_markdown_via_openapi(
                    token, self.base_url, export_markdown, conv_timeout
                )
                converted_children_id, converted_descendants = _convert_data_to_descendant_payload(conv_data)
                used_convert = bool(converted_children_id and converted_descendants)
            except Exception as exc:
                logger.warning(
                    "Feishu Markdown→blocks 官方转换失败，改用内置解析: %s",
                    exc,
                )

        if used_convert:
            try:
                self._write_converted_descendants(
                    doc_id,
                    token,
                    converted_children_id,
                    converted_descendants,
                )
                block_count = len(converted_descendants)
            except Exception as exc:
                logger.warning(
                    "Feishu 官方转换块写入失败，改用内置解析: %s",
                    exc,
                )
                used_convert = False

        if not used_convert:
            flat, image_refs = _markdown_to_feishu_blocks_with_image_refs(
                export_markdown,
                image_resolver=image_resolver,
            )
            created_ids = self._write_flat_blocks_batched(doc_id, token, flat)
            image_upload_count, image_upload_errors = self._upload_flat_image_refs(
                doc_id,
                token,
                image_refs,
                created_ids,
            )
            block_count = len(flat)

        md_nonempty = bool((export_markdown or "").strip())
        if md_nonempty and block_count == 0:
            raise RuntimeError(
                "摘要内容未能解析为飞书文档块（0 块）。请检查 Markdown 是否为空或仅含不支持的语法。"
            )

        response = {
            "ok": True,
            "doc_token": doc_id,
            "block_count": block_count,
            "image_upload_count": image_upload_count,
            "image_upload_errors": image_upload_errors,
            "via": "openapi_convert" if used_convert else "legacy_markdown",
            "markdown_format": "feishu_normalized",
            "auth_mode": auth_mode,
            "export_destination": export_destination,
            "url": _public_wiki_url(wiki_location["node_token"], self.base_url)
            if wiki_location else _public_docx_url(doc_id, self.base_url),
        }
        if wiki_location:
            response.update({
                "wiki_space_id": wiki_location["space_id"],
                "wiki_node_token": wiki_location["node_token"],
            })
        return response


def export_markdown_to_lark(
    title: str,
    markdown: str,
    *,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    folder_token: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    user_access_token: Optional[str] = None,
    timeout: int = 15,
    dry_run: bool = False,
    task_id: Optional[str] = None,
    artifact_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Convenience wrapper used by the pipeline."""
    exporter = LarkExporter(
        app_id=app_id,
        app_secret=app_secret,
        base_url=base_url,
        user_access_token=user_access_token,
        timeout=timeout,
        dry_run=dry_run,
    )
    return exporter.create_doc_markdown(
        title,
        markdown,
        folder_token=folder_token,
        task_id=task_id,
        artifact_root=artifact_root,
    )


__all__ = ["LarkExporter", "export_markdown_to_lark", "markdown_to_feishu_blocks"]
