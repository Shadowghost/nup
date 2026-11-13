"""NUP dump discovery: source layout conventions and the in-memory tree model.

Recognizes dump directory layouts (base/updates/dlc bundles, update-only
dumps, or a single content dir), resolves each package's identity from its
ExeFS (NPDM program ID, NSO/NRO build ID), and walks source trees into the
DirObj/FileObj/Tree model the packer serializes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import struct
import sys
from typing import Final

from . import format as F

# SPEC §6.3 reserved artifact names
ARTIFACT_NAMES: Final = {b".listing"}
# child-slot marker: slot >= FILE_TAG references files[]
FILE_TAG: Final = 0x8000_0000
_DEC: Final = re.compile(r"^\d+$")
_HEX: Final = re.compile(r"^[0-9a-fA-F]{16}$")


def parse_npdm_program_id(data: bytes) -> int | None:
    """Extract the program ID from an NPDM (any known header revision).

    ACI0 layout: magic 'ACI0' (4), reserved (0xC), program id (u64 LE) at +0x10.
    """
    off = data.find(b"ACI0")
    if off < 0 or off + 0x18 > len(data):
        return None
    return int(struct.unpack_from("<Q", data, off + 0x10)[0])


@dataclass(slots=True)
class DirObj:
    name: bytes
    children: list[DirObj | FileObj] = field(default_factory=list)
    id: int = -1
    first_child: int = 0
    child_count: int = 0


@dataclass(slots=True)
class FileObj:
    name: bytes
    src_path: str
    size: int = 0
    sha256: bytes = b"\0" * 32
    extent_offset: int = 0
    extent_size: int = 0
    codec: F.Codec = F.CODEC_RAW
    codec_flags: int = 0


@dataclass(slots=True)
class PackInput:
    """One package to store: kind, ids, and the source directory of its tree."""

    kind: F.Kind
    src: str
    title_id: int = 0
    version: int = 0
    flags: int = 0
    npdm: bytes | None = None
    build_id: bytes | None = None
    program_index: int = 0  # SPEC §3.1.5
    parent_id: int = 0      # SPEC §3.1.6: the add-on an addon_update patches
    skip: frozenset[bytes] = frozenset()  # root entries left out (Yoink's romfs.img)


def _find_npdm(src: str) -> str | None:
    for cand in ("exefs/main.npdm", "main.npdm"):
        p = os.path.join(src, cand)
        if os.path.isfile(p):
            return p
    return None


def _read_build_id(src: str) -> bytes | None:
    """ModuleId: bytes 0x40..0x60 of exefs/main (NSO0/NRO0 build-id field).

    Returns None when main is absent, shorter than 0x60 bytes, or has an
    unknown magic - the TLV is optional metadata.
    """
    for cand in ("exefs/main", "main"):
        p = os.path.join(src, cand)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                head = fh.read(0x60)
            if len(head) == 0x60 and (head[:4] == b"NSO0" or head[0x10:0x14] == b"NRO0"):
                return head[0x40:0x60]
            return None
    return None


def _resolve_title_id(pkg: PackInput, override: int | None) -> None:
    npdm_path = _find_npdm(pkg.src)
    if npdm_path:
        with open(npdm_path, "rb") as fh:
            pkg.npdm = fh.read()
        pid = parse_npdm_program_id(pkg.npdm)
        if pid is not None and pid != 0:
            pkg.title_id = pid
    pkg.build_id = _read_build_id(pkg.src)
    if override is not None:
        pkg.title_id = override


def _looks_like_content(d: str) -> bool:
    return os.path.isdir(os.path.join(d, "exefs")) or os.path.isdir(os.path.join(d, "romfs"))


def _has_files(d: str) -> bool:
    """True when a package dir contains at least one packable file. Dump-tool
    artifacts don't count, and neither do folders that only hold empty dirs -
    an exefs/ folder with no files is not a usable ExeFS, and a package whose
    record points at one misleads consumers (they treat it as the ExeFS)."""
    for _, _, filenames in os.walk(d):
        for name in filenames:
            if name != ".listing":
                return True
    return False


def _check_untarred(d: str) -> None:
    """Refuse OunceCake --tar output (<section>.tar in place of the folder)."""
    tars = [s for s in ("exefs", "romfs") if os.path.isfile(os.path.join(d, s + ".tar"))
            and not os.path.isdir(os.path.join(d, s))]
    if tars:
        raise SystemExit(f"pack: {d!r} holds {', '.join(t + '.tar' for t in tars)} (OunceCake's --tar "
                         f"mode): unpack each into a folder of its name beside it first, e.g. "
                         f"tar -xf romfs.tar -C romfs")


def _root_skip(d: str) -> frozenset[bytes]:
    """Root files that duplicate a section (Yoink's romfs.img beside romfs/)."""
    out = {f"{s}.img".encode() for s in ("romfs", "exefs")
           if os.path.isfile(os.path.join(d, s + ".img")) and os.path.isdir(os.path.join(d, s))}
    for name in sorted(out):
        print(f"pack: {os.path.join(d, name.decode())}: an image of the folder beside it, left out",
              file=sys.stderr)
    return frozenset(out)


def _programs(vdir: str) -> list[tuple[int, str]]:
    """(program index, content dir) pairs of a version folder: program/<index>/
    in the console layout (OunceCake), else the folder itself as program 0."""
    pdir = os.path.join(vdir, "program")
    indexes = sorted(int(n) for n in os.listdir(pdir) if _DEC.match(n)) if os.path.isdir(pdir) else []
    if not indexes:
        _check_untarred(vdir)
        return [(0, vdir)] if _has_files(vdir) else []
    if _looks_like_content(vdir):
        raise SystemExit(f"pack: {vdir!r} holds both exefs/ or romfs/ and program/<index>/: "
                         f"one layout per version folder")
    out: list[tuple[int, str]] = []
    for i in indexes:
        for cand in (os.path.join(pdir, str(i)), os.path.join(pdir, str(i), "program")):
            _check_untarred(cand)
            if _looks_like_content(cand) and _has_files(cand):
                out.append((i, cand))
                break
    return out


def _collect_version(kind: F.Kind, vdir: str, version: int, title_id: int | None,
                     all_programs: bool, skipped: list[str]) -> list[PackInput]:
    """Packages of one base/update version folder (SPEC §3.1.5). Programs other
    than 0 only with all_programs, else they are added to skipped."""
    out: list[PackInput] = []
    for idx, src in _programs(vdir):
        if idx and not all_programs:
            skipped.append(src)
            continue
        p = PackInput(kind, src, version=version, program_index=idx, skip=_root_skip(src))
        _resolve_title_id(p, title_id if (idx == 0 and kind == F.KIND_BASE) else None)
        if p.title_id == 0 and title_id:
            p.title_id = title_id + idx
        out.append(p)
    return out


def _collect_updates(upd_dir: str, base_title_id: int, all_programs: bool,
                     skipped: list[str]) -> list[PackInput]:
    """updates/<decimal version>/ content dirs."""
    out: list[PackInput] = []
    for name in sorted((n for n in os.listdir(upd_dir) if _DEC.match(n)), key=int):
        p = os.path.join(upd_dir, name)
        if os.path.isdir(p):
            out.extend(_collect_version(F.KIND_UPDATE, p, int(name, 10), base_title_id,
                                        all_programs, skipped))
    return out


def _collect_switch2(src: str, app_id: int, all_programs: bool,
                     skipped: list[str]) -> list[PackInput]:
    """Yoink's switch2/base/ and switch2/updates/<v>/: a Switch 2 Edition's program 1."""
    out: list[PackInput] = []
    root = os.path.join(src, "switch2")
    folders = [(F.KIND_BASE, 0, os.path.join(root, "base"))]
    udir = os.path.join(root, "updates")
    folders += [(F.KIND_UPDATE, int(n, 10), os.path.join(udir, n))
                for n in sorted((n for n in os.listdir(udir) if _DEC.match(n)), key=int)] if os.path.isdir(udir) else []
    for kind, version, d in folders:
        if not os.path.isdir(d):
            continue
        _check_untarred(d)
        if not (_looks_like_content(d) and _has_files(d)):
            continue
        if not all_programs:
            skipped.append(d)
            continue
        p = PackInput(kind, d, version=version, program_index=1, skip=_root_skip(d))
        _resolve_title_id(p, None)
        if p.title_id == 0 and app_id:
            p.title_id = app_id + 1
        out.append(p)
    return out


CNMT_TYPE_ADDON: Final = 0x82
CNMT_TYPE_DATA_PATCH: Final = 0x84


def _cnmt_records(content: str) -> list[tuple[int, int, int]]:
    """(id, type, DataId) of the CNMT records in <content>/meta/ (switchbrew layout)."""
    meta = os.path.join(content, "meta")
    out: list[tuple[int, int, int]] = []
    for name in sorted(os.listdir(meta)) if os.path.isdir(meta) else []:
        if not name.endswith(".cnmt"):
            continue
        with open(os.path.join(meta, name), "rb") as fh:
            b = fh.read(0x28)
        if len(b) < 0x20:
            raise SystemExit(f"pack: {os.path.join(meta, name)!r}: not a content meta record")
        rid, _ver, typ, _, ext = struct.unpack_from("<QIBBH", b, 0)
        data_id = 0
        if typ == CNMT_TYPE_DATA_PATCH and ext >= 8 and len(b) >= 0x28:
            data_id = struct.unpack_from("<Q", b, 0x20)[0]
        out.append((rid, typ, data_id))
    return out


def _addon_content(pkg_dir: str) -> str | None:
    """An add-on package's content dir: program/0/ or program/0/data/."""
    for cand in (os.path.join(pkg_dir, "program", "0"), os.path.join(pkg_dir, "program", "0", "data")):
        if _looks_like_content(cand) and _has_files(cand):
            return cand
    return None


def _collect_dlcs(dlc_dir: str) -> list[PackInput]:
    """dlc/<16-hex title id>/<index>/ content dirs (SPEC §3.1.2 addon ids), or the
    console layout dlc/<title id>/<version>/{base,updates/<v>}/ (SPEC §3.1.6)."""
    out: list[PackInput] = []
    for name in sorted(os.listdir(dlc_dir)):
        tdir = os.path.join(dlc_dir, name)
        if not _HEX.match(name) or not os.path.isdir(tdir):
            continue
        tid = int(name, 16)
        for idx in sorted(os.listdir(tdir)):
            cdir = os.path.join(tdir, idx)
            if not os.path.isdir(cdir):
                continue
            if not (os.path.isdir(os.path.join(cdir, "base")) or os.path.isdir(os.path.join(cdir, "updates"))):
                _check_untarred(cdir)
                if not _has_files(cdir):
                    continue
                dp = [r for r in _cnmt_records(cdir) if r[1] == CNMT_TYPE_DATA_PATCH]
                if dp or tid & 0x800:
                    # Yoink: data patch beside its add-on (add-on = CNMT DataId, else id - 0x800)
                    out.append(PackInput(F.KIND_ADDON_UPDATE, cdir, title_id=tid,
                                         version=int(idx, 10) if _DEC.match(idx) else 0,
                                         parent_id=dp[0][2] if dp and dp[0][2] else tid - 0x800,
                                         skip=_root_skip(cdir)))
                else:
                    out.append(PackInput(F.KIND_ADDON, cdir, title_id=tid, skip=_root_skip(cdir)))
                continue
            if not _DEC.match(idx):
                raise SystemExit(f"pack: {cdir!r}: an add-on version folder is named by its version number")
            base = _addon_content(os.path.join(cdir, "base"))
            if base:
                bad = [r for r in _cnmt_records(base) if r[1] == CNMT_TYPE_ADDON and r[0] != tid]
                if bad:
                    raise SystemExit(f"pack: {base!r}: its content meta record is add-on "
                                     f"{bad[0][0]:016X}, the folder is {tid:016X}")
                out.append(PackInput(F.KIND_ADDON, base, title_id=tid, version=int(idx, 10)))
            udir = os.path.join(cdir, "updates")
            for pv in sorted((n for n in os.listdir(udir) if _DEC.match(n)), key=int) if os.path.isdir(udir) else []:
                content = _addon_content(os.path.join(udir, pv))
                if content is None:
                    continue
                dp = [r for r in _cnmt_records(content) if r[1] == CNMT_TYPE_DATA_PATCH]
                if dp and dp[0][2] != tid:
                    raise SystemExit(f"pack: {content!r}: its content meta record is data patch "
                                     f"{dp[0][0]:016X} of add-on {dp[0][2]:016X}, the folder is {tid:016X}")
                # id from the CNMT, else add-on id + 0x800
                out.append(PackInput(F.KIND_ADDON_UPDATE, content, title_id=dp[0][0] if dp else tid + 0x800,
                                     version=int(pv, 10), parent_id=tid))
    return out


def discover(src: str, base_title_id: int | None, all_programs: bool = False) -> list[PackInput]:
    """Discover packages in a dump directory (SPEC §3.1.3 conventions).
    all_programs also takes programs other than 0 (SPEC §3.1.5)."""
    packages: list[PackInput] = []
    skipped: list[str] = []
    upd_dir = os.path.join(src, "updates")
    has_updates = os.path.isdir(upd_dir)

    if os.path.isdir(os.path.join(src, "base")):
        bases = _collect_version(F.KIND_BASE, os.path.join(src, "base"), 0, base_title_id,
                                 all_programs, skipped)
        packages.extend(bases)
        app_id = next((b.title_id for b in bases if b.program_index == 0), base_title_id or 0)
        # an empty base/ is not a game: drop it so a real update can boot
        # as the standalone program instead of hiding behind a dead base
        if has_updates:
            packages.extend(_collect_updates(upd_dir, app_id, all_programs, skipped))
        packages.extend(_collect_switch2(src, app_id, all_programs, skipped))
        dlc_dir = os.path.join(src, "dlc")
        if os.path.isdir(dlc_dir):
            packages.extend(_collect_dlcs(dlc_dir))
    elif has_updates:
        # Update-only dump:
        # every update package carries standalone_update (SPEC §3.1.2, flags
        # bit 0 at offset 0x19). DLC may still ride along in dlc/.
        packages.extend(_collect_updates(upd_dir, base_title_id or 0, all_programs, skipped))
        packages.extend(_collect_switch2(src, base_title_id or 0, all_programs, skipped))
        dlc_dir = os.path.join(src, "dlc")
        if os.path.isdir(dlc_dir):
            packages.extend(_collect_dlcs(dlc_dir))
    elif _looks_like_content(src):
        base = PackInput(F.KIND_BASE, src)
        _resolve_title_id(base, base_title_id)
        packages.append(base)
    else:
        raise SystemExit(
            f"pack: {src!r} is neither a dump bundle (base/updates/dlc), an update-only "
            f"dump (updates/), nor a content directory (exefs/ or romfs/)")
    for s in skipped:
        print(f"pack: {s}: another program of a multi-program application, left out "
              f"(--all-programs stores it; such a file needs a NUP 1.1 reader)", file=sys.stderr)
    if not packages:
        raise SystemExit(f"pack: no packages found in {src!r}")
    return packages


@dataclass(slots=True)
class Tree:
    """Node store for one pack run (global across packages)."""

    artifacts: set[bytes]
    dirs: list[DirObj] = field(default_factory=list)
    files: list[FileObj] = field(default_factory=list)
    # serial ids; see assign()
    children: list[int] = field(default_factory=list)
    pool: dict[bytes, int] = field(default_factory=dict)

    @property
    def pool_size(self) -> int:
        return sum(map(len, self.pool))

    def intern(self, name: bytes) -> int:
        off = self.pool.get(name)
        if off is None:
            off = self.pool_size
            self.pool[name] = off
        return off

    def add_tree(self, src: str, skip: frozenset[bytes] = frozenset()) -> DirObj:
        return self._walk(src, b"", skip)

    def _walk(self, src: str, name: bytes, skip: frozenset[bytes] = frozenset()) -> DirObj:
        node = DirObj(name)
        try:
            entries = sorted(os.scandir(src),
                             key=lambda e: e.name.encode("utf-8", "surrogateescape"))
        except OSError as exc:
            raise SystemExit(f"pack: cannot read {src}: {exc}")
        for entry in entries:
            ename = entry.name.encode("utf-8", "surrogateescape")
            if ename in skip:
                continue
            if entry.is_dir(follow_symlinks=False):
                node.children.append(self._walk(entry.path, ename))
            elif ename not in self.artifacts:
                node.children.append(FileObj(ename, entry.path))
        return node

    def assign(self, roots: list[DirObj]) -> None:
        """Assign serial order: dirs pre-order, files in first-reference order,
        contiguous child slots per directory."""
        def rec(d: DirObj) -> int:
            did = len(self.dirs)
            d.id = did
            self.dirs.append(d)
            slot_base = len(self.children)
            self.children.extend([0] * len(d.children))
            for i, child in enumerate(d.children):
                if isinstance(child, FileObj):
                    self.children[slot_base + i] = FILE_TAG + len(self.files)
                    self.files.append(child)
            for i, child in enumerate(d.children):
                if isinstance(child, DirObj):
                    self.children[slot_base + i] = rec(child)
            d.first_child = slot_base
            d.child_count = len(d.children)
            return did

        for root in roots:
            rec(root)
