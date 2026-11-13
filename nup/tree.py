"""Dump-tree walking: container paths, ordering rules, and exclusions."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import PurePosixPath

from .errors import NupError
from .layout import ARTIFACT_SUFFIXES, DELETED_NAME, META_NAME

READ_CHUNK = 4 << 20
PREFIX_SIZE = 0x200


@dataclass(slots=True)
class SourceFile:
    """One packable file: its container path and where the bytes live."""

    container_path: str
    fs_path: str
    size: int


def validate_container_path(path: str) -> None:
    if not path or path != path.strip():
        raise NupError(f"invalid entry path {path!r}")
    if path.startswith("/") or "//" in path or path.endswith("/"):
        raise NupError(f"invalid entry path {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise NupError(f"invalid entry path {path!r}")
    if "\\" in path:
        raise NupError(f"entry path {path!r} contains a backslash")


def walk_content_dir(root: str, scope: str) -> list[SourceFile]:
    sources = []
    images = section_images(root)
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if filename.endswith(ARTIFACT_SUFFIXES):
                continue
            if dirpath == root and filename in images:
                continue
            if filename in (META_NAME, DELETED_NAME):
                raise NupError(
                    f"{filename!r} at {dirpath!r} is reserved for the container"
                )
            fs_path = os.path.join(dirpath, filename)
            relative = os.path.relpath(fs_path, root).replace(os.sep, "/")
            container_path = f"{scope}/{relative}"
            validate_container_path(container_path)
            sources.append(SourceFile(container_path, fs_path, os.path.getsize(fs_path)))
    sources.sort(key=lambda source: source.container_path.encode("utf-8"))
    return sources


def section_images(root: str) -> set[str]:
    """Root files that duplicate a section: Yoink's romfs.img beside romfs/."""
    return {
        f"{section}.img" for section in ("exefs", "romfs")
        if os.path.isfile(os.path.join(root, f"{section}.img"))
        and os.path.isdir(os.path.join(root, section))
    }


def hash_source(source: SourceFile) -> tuple[bytes, bytes]:
    """Whole-file SHA-256 and prefix SHA-256 (first 0x200 bytes)."""
    whole = hashlib.sha256()
    with open(source.fs_path, "rb") as handle:
        remaining = source.size
        while remaining:
            chunk = handle.read(min(READ_CHUNK, remaining))
            if not chunk:
                raise NupError(f"{source.fs_path!r} changed while packing")
            whole.update(chunk)
            remaining -= len(chunk)
    with open(source.fs_path, "rb") as handle:
        head = handle.read(PREFIX_SIZE)
    return whole.digest(), hashlib.sha256(head).digest()


def hash_bytes(data: bytes) -> tuple[bytes, bytes]:
    return hashlib.sha256(data).digest(), hashlib.sha256(data[:PREFIX_SIZE]).digest()


def iter_file_chunks(fs_path: str, size: int):
    with open(fs_path, "rb") as handle:
        remaining = size
        while remaining:
            chunk = handle.read(min(READ_CHUNK, remaining))
            if not chunk:
                raise NupError(f"{fs_path!r} changed while writing")
            remaining -= len(chunk)
            yield chunk


def encode_deleted_manifest(paths: list[str]) -> bytes:
    return b"".join(path.encode("utf-8") + b"\n" for path in sorted(paths))


def parse_deleted_manifest(data: bytes) -> list[str]:
    return [line.decode("utf-8") for line in data.split(b"\n") if line]
