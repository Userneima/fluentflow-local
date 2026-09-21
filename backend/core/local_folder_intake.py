"""Take a folder on this machine and work on the files where they are.

The owner's reason for this entry: the cut file should end up beside the recording
it came from. That is impossible for a browser upload — the browser hands over
bytes, never the folder the file was picked from, so the product has no way to
know where "beside it" is. Naming the folder is what makes the answer available,
and it is also how the batch scripts in ``agent_studio`` have always worked.

Reading and writing inside a directory the user names is a capability this product
did not have, so the boundaries are here rather than spread across a route:

- **Local edition only.** A hosted server accepting a filesystem path would be
  reading its own disk on a stranger's request. There is no hosted route for this
  and there must not be one.
- **Nothing is overwritten and nothing is deleted.** The cut file is written under
  a new name beside the original; if that name is taken, the next free one is used
  and the choice is recorded. The recording itself is opened read-only.
- **The folder is listed, not walked.** One level, no recursion: a user pointing at
  their Movies folder should get the recordings in it, not everything underneath
  it. Files already produced by a previous pass are skipped, so running the same
  folder twice does not cut the cuts.

The path is validated here and refused with a sentence, because every failure mode
is something the user can see and fix: a typo, a folder that is really a file, a
directory this process cannot read, an empty folder.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from backend.core.media_intake import AUDIO_SUFFIXES, VIDEO_SUFFIXES
from backend.core.result_artifacts import CUT_FILE_NAME_MARKER
from backend.core.storage_cleanup import is_managed_path

# What is worth transcribing out of a folder. Subtitle and text files are left
# out on purpose: this entry exists for recordings, and a folder of notes would
# queue a pile of tasks nobody asked for.
INTAKE_SUFFIXES = frozenset(VIDEO_SUFFIXES | AUDIO_SUFFIXES)

# Everything this flow produces beside an original carries this marker, so a
# second pass over the same folder skips it instead of cutting a cut file. Shared
# with the cut itself, which checks the same marker plus a mark inside the bytes.
CUT_FILE_MARKER = CUT_FILE_NAME_MARKER

# A folder can hold hundreds of recordings, and each one is minutes of CPU plus a
# Claude call. Beyond this the answer is "say how many there are and let the user
# narrow it down", not "queue them all".
MAX_FILES_PER_FOLDER = 30


class FolderIntakeError(RuntimeError):
    """A reason worth showing the user, in their language."""


@dataclass(frozen=True)
class FolderListing:
    folder: Path
    files: list[Path]
    skipped_cut_files: int
    skipped_unsupported: int
    total_media: int

    @property
    def truncated(self) -> bool:
        return self.total_media > len(self.files)


def resolve_folder(raw: str | None) -> Path:
    """The folder this machine will read, or a refusal naming what is wrong."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise FolderIntakeError("请填一个本机文件夹路径。")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise FolderIntakeError(f"请用完整路径（以 / 开头），现在填的是「{text}」。")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise FolderIntakeError(f"这个路径读不了：{exc}") from exc
    if not resolved.exists():
        raise FolderIntakeError(f"找不到这个文件夹：{resolved}")
    if resolved.is_file():
        raise FolderIntakeError(
            f"这是一个文件，不是文件夹。填它所在的文件夹：{resolved.parent}"
        )
    if not resolved.is_dir():
        raise FolderIntakeError(f"这不是一个文件夹：{resolved}")
    if not os.access(resolved, os.R_OK):
        raise FolderIntakeError(f"没有读取这个文件夹的权限：{resolved}")
    if is_managed_path(resolved) or _is_storage_root(resolved):
        # Pointing this at FluentFlow's own storage would re-ingest its own
        # outputs, and "beside the original" would mean inside the store.
        raise FolderIntakeError("这是 FluentFlow 自己存文件的目录，选你自己放素材的文件夹。")
    return resolved


def _is_storage_root(path: Path) -> bool:
    from backend.core.storage_cleanup import managed_roots

    return any(path == root or root in path.parents for root in managed_roots())


def list_media(folder: Path, *, limit: int = MAX_FILES_PER_FOLDER) -> FolderListing:
    """The recordings in this folder, one level deep, in a stable order."""
    files: list[Path] = []
    skipped_cut = 0
    skipped_other = 0
    for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        suffix = entry.suffix.lower()
        if suffix not in INTAKE_SUFFIXES:
            skipped_other += 1
            continue
        if CUT_FILE_MARKER in entry.stem:
            skipped_cut += 1
            continue
        files.append(entry)
    if not files:
        raise FolderIntakeError(
            "这个文件夹里没有可处理的音视频文件"
            + (f"（跳过了 {skipped_cut} 个之前剪好的文件）。" if skipped_cut else "。")
        )
    return FolderListing(
        folder=folder,
        files=files[: max(1, limit)],
        skipped_cut_files=skipped_cut,
        skipped_unsupported=skipped_other,
        total_media=len(files),
    )


def resolve_media_file(raw: str | None) -> Path:
    """One recording this machine will read, or a refusal naming what is wrong.

    The single-file counterpart of ``resolve_folder``, for a path that came back
    from the system file dialog. Same rules, because the risk is the same: it must
    exist, be readable, be a recording, and not live inside FluentFlow's own store
    — where "beside the original" would mean inside the store, and where its own
    outputs would be re-ingested.
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise FolderIntakeError("没有拿到文件路径。")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise FolderIntakeError(f"需要完整路径，现在是「{text}」。")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise FolderIntakeError(f"这个文件读不了：{exc}") from exc
    if resolved.is_dir():
        raise FolderIntakeError(f"这是一个文件夹，不是文件：{resolved}")
    if not resolved.is_file():
        raise FolderIntakeError(f"找不到这个文件：{resolved}")
    if not os.access(resolved, os.R_OK):
        raise FolderIntakeError(f"没有读取这个文件的权限：{resolved}")
    if resolved.suffix.lower() not in INTAKE_SUFFIXES:
        raise FolderIntakeError(
            f"不支持这种文件（{resolved.suffix or '无扩展名'}），这里只处理音视频。"
        )
    if CUT_FILE_MARKER in resolved.stem:
        raise FolderIntakeError(
            f"这看起来是之前剪好的文件（{resolved.name}），选原片就行，不用再剪一遍。"
        )
    if is_managed_path(resolved) or _is_storage_root(resolved):
        raise FolderIntakeError("这是 FluentFlow 自己存文件的目录里的文件，选你自己的原片。")
    return resolved


# How far apart two timestamps may be and still be the same file. The browser
# reports whole milliseconds and filesystems keep more precision than that, so an
# exact comparison would never match.
_MTIME_TOLERANCE_SECONDS = 2.0


def locate_dropped_file(
    name: str,
    size_bytes: int,
    folders: Sequence[str | Path],
    modified_ms: float | None = None,
) -> Path | None:
    """Find where a dropped file lives, or admit that we cannot.

    A browser hands a dropped file's *contents* to the page along with its name,
    size and modification time, and deliberately never its folder — a web page
    must not learn the shape of somebody's disk. The consequence is that a drop
    has to be uploaded: a second copy of a gigabyte written into the store, and no
    "next to the original" for the cut file to go to.

    This edition is not a web page. It runs on the machine that holds the file, so
    it can look, and looking is cheap: nine folders and fifty-nine entries measured
    at 3ms. The search is bounded to folders this owner has already pointed
    FluentFlow at — not a scan of the disk, which would be both slow and a much
    larger claim on their privacy than they made.

    Never guesses. Two files matching every field is unlikely, but if it happens
    the right answer is "I do not know", because delivering a cut file beside the
    wrong copy is worse than not delivering it. ``None`` means the caller uploads,
    which is exactly what it does today.
    """
    wanted = str(name or "").strip()
    if not wanted or size_bytes <= 0:
        return None
    matches: list[Path] = []
    for folder in folders:
        candidate = Path(folder).expanduser() / wanted
        try:
            info = candidate.stat()
        except OSError:
            continue
        if not candidate.is_file() or info.st_size != size_bytes:
            continue
        if modified_ms is not None and abs(info.st_mtime - modified_ms / 1000.0) > _MTIME_TOLERANCE_SECONDS:
            continue
        resolved = candidate.resolve()
        if resolved not in matches:
            matches.append(resolved)
    return matches[0] if len(matches) == 1 else None


def cut_file_target(source: Path) -> Path:
    """Where the cut version of this recording goes: beside it, under a new name.

    Never the original's own name, and never a name already taken. A numbered
    suffix is used rather than overwriting, because the file that is already there
    might be the one the user is working from.
    """
    folder = source.parent
    base = f"{source.stem}{CUT_FILE_MARKER}"
    suffix = source.suffix.lower() or ".mp4"
    candidate = folder / f"{base}{suffix}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{base} ({index}){suffix}"
        index += 1
        if index > 99:  # pragma: no cover - a folder with 99 of these is a mistake
            raise FolderIntakeError(f"同名文件太多了，先清一下：{folder}")
    return candidate


def describe(listing: FolderListing) -> dict[str, object]:
    """What the caller is about to queue, in numbers it can show before it starts."""
    return {
        "folder": str(listing.folder),
        "count": len(listing.files),
        "total_media": listing.total_media,
        "truncated": listing.truncated,
        "limit": MAX_FILES_PER_FOLDER,
        "skipped_cut_files": listing.skipped_cut_files,
        "skipped_unsupported": listing.skipped_unsupported,
        "files": [item.name for item in listing.files],
    }


__all__ = [
    "CUT_FILE_MARKER",
    "INTAKE_SUFFIXES",
    "MAX_FILES_PER_FOLDER",
    "FolderIntakeError",
    "FolderListing",
    "cut_file_target",
    "describe",
    "resolve_media_file",
    "list_media",
    "resolve_folder",
]
