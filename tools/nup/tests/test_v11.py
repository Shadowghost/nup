"""NUP 1.1: multi-program packages (SPEC §3.1.5), addon_update packages (§3.1.6),
the dump layouts of the extractors (README), and files of other producers (§3.1.7).

Run: python -m unittest discover -s tools/nup/tests   (from the repository root)
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from nup import format as F  # noqa: E402
from nup.edit import edit  # noqa: E402
from nup.pack import pack  # noqa: E402
from nup.read import NupReader  # noqa: E402
from nup.verify import verify  # noqa: E402

APP = 0x010015100B514000
S2 = 0x040015100B514001       # a Switch 2 Edition's program 1 (its own NPDM id)
ADDON = APP + 0x1001          # the Upgrade Pack
PATCH = ADDON + 0x800         # its data patch
V = 327680


def npdm(pid: int) -> bytes:
    b = bytearray(0x100)
    b[:4] = b"META"
    struct.pack_into("<II", b, 0x70, 0x80, 0x20)
    b[0x80:0x84] = b"ACI0"
    struct.pack_into("<Q", b, 0x90, pid)
    return bytes(b)


def nso(seed: int) -> bytes:
    b = bytearray(0x100)
    b[:4] = b"NSO0"
    b[0x40:0x60] = bytes((seed + i) & 0xFF for i in range(32))
    return bytes(b)


def cnmt(rid: int, ver: int, typ: int, ext: bytes) -> bytes:   # switchbrew's layout, no contents
    return struct.pack("<QIBBHHHB3xI4x", rid, ver, typ, 1, len(ext), 0, 0, 0, 0) + ext + b"\0" * 0x20


def put(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def content(d: str, pid: int | None, marker: str) -> None:
    """exefs/ (when pid) and romfs/ of one program"""
    if pid is not None:
        put(os.path.join(d, "exefs", "main.npdm"), npdm(pid))
        put(os.path.join(d, "exefs", "main"), nso(pid & 0xFF))
    put(os.path.join(d, "romfs", "data", f"{marker}.bin"), marker.encode() * 50)
    put(os.path.join(d, "romfs", "shared.bin"), b"same in every package" * 20)


def records(path: str) -> list[tuple[int, int, int, int, int, int]]:
    with NupReader(path) as r:
        return [(p.kind, p.title_id, p.version, p.program_index, p.parent_id, p.flags) for p in r.packages]


def sb_flags(path: str) -> int:
    with NupReader(path) as r:
        return r.super["flags"]


def rewrite(path: str, change, sb_flags_value: int | None = None) -> None:
    """Rewrite package records as another producer would write them (change(i, Package) -> Package),
    then rehash the TOC: files of other producers without needing those tools"""
    with open(path, "r+b") as fh:
        data = bytearray(fh.read())
        s = F.parse_super(bytes(data[:F.HEADER_SIZE])) if sb_flags_value is None else None
        toc_off = F.HEADER_SIZE
        toc_size = struct.unpack_from("<Q", data, 0x10)[0]
        toc = data[toc_off:toc_off + toc_size]
        n = struct.unpack_from("<I", toc, 4)[0]
        for i in range(n):
            pos = F._toc_head_s.size + i * F.PKG_SIZE
            p = F.parse_pkg(bytes(toc), pos)
            F._pkg_s.pack_into(toc, pos, *F.pkg_fields(change(i, p)))
        data[toc_off:toc_off + toc_size] = toc
        data[0x18:0x38] = hashlib.sha256(bytes(toc)).digest()
        if sb_flags_value is not None:
            struct.pack_into("<I", data, 0x4C, sb_flags_value)
        fh.seek(0)
        fh.write(data)
        del s


class Case(unittest.TestCase):
    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = td.name
        self.dump = os.path.join(self.tmp, "Game")

    def pack(self, name: str, **kw) -> str:
        out = os.path.join(self.tmp, name)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            pack(out, self.dump, **kw)
        self.stderr = err.getvalue()
        return out

    def assertVerifies(self, path: str) -> None:
        ok, problems = verify(path, deep=True)
        self.assertTrue(ok, problems)


class Flattened(Case):
    def test_a_1_0_dump_gives_a_1_0_file(self) -> None:
        content(os.path.join(self.dump, "base"), APP, "base")
        content(os.path.join(self.dump, "updates", str(V)), APP, "upd")
        content(os.path.join(self.dump, "dlc", f"{ADDON:016x}", "0"), None, "dlc")
        out = self.pack("g.nup")
        self.assertEqual(sb_flags(out), 0)
        self.assertEqual(records(out), [(0, APP, 0, 0, 0, 0), (1, APP, V, 0, 0, 0), (2, ADDON, 0, 0, 0, 0)])
        with open(out, "rb") as fh:
            toc = fh.read(F.HEADER_SIZE + 0x30 + 3 * F.PKG_SIZE)[F.HEADER_SIZE + 0x30:]
        for i in range(3):   # the 1.1 fields stay zero: 1.0 readers see exactly what they saw
            rec = toc[i * F.PKG_SIZE:(i + 1) * F.PKG_SIZE]
            self.assertEqual(rec[0x1A:0x20] + rec[0x2C:0x40], b"\0" * 26)
        self.assertVerifies(out)


def console_dump(dump: str) -> None:
    """OunceCake's layout: base/program/<i>/, updates/<v>/program/<i>/, dlc/<id>/<v>/base and .../updates/<pv>/,
    with the content meta records it serves in meta/"""
    content(os.path.join(dump, "base", "program", "0"), APP, "base")
    content(os.path.join(dump, "updates", str(V), "program", "0"), APP, "upd")
    put(os.path.join(dump, "updates", str(V), "program", "0", "control", "control.nacp"), b"nacp")
    content(os.path.join(dump, "updates", str(V), "program", "1"), S2, "s2")
    d = os.path.join(dump, "dlc", f"{ADDON:016x}", "0")
    content(os.path.join(d, "base", "program", "0"), None, "upgrade")
    put(os.path.join(d, "base", "program", "0", "meta", "AddOnContent.cnmt"),
        cnmt(ADDON, 0, 0x82, struct.pack("<QIB3xQ", APP, 196608, 0, PATCH)))
    content(os.path.join(d, "updates", "131072", "program", "0"), None, "patched")
    put(os.path.join(d, "updates", "131072", "program", "0", "meta", "DataPatch.cnmt"),
        cnmt(PATCH, 131072, 0x84, struct.pack("<QQII8x", ADDON, APP, V, 0)))


def yoink_dump(dump: str) -> None:
    """Yoink's FTP tree: flattened program 0, switch2/updates/<v>/ (program 1), the data patch beside its add-on
    as dlc/<data patch id>/<v>/romfs, and a romfs.img copy beside a RomFS folder"""
    content(os.path.join(dump, "base"), APP, "base")
    content(os.path.join(dump, "updates", str(V)), APP, "upd")
    put(os.path.join(dump, "updates", str(V), "romfs.img"), b"image" * 100)
    content(os.path.join(dump, "switch2", "updates", str(V)), S2, "s2")
    content(os.path.join(dump, "dlc", f"{ADDON:016x}", "0"), None, "upgrade")
    content(os.path.join(dump, "dlc", f"{PATCH:016x}", "131072"), None, "patched")


class ConsoleLayout(Case):
    def setUp(self) -> None:
        super().setUp()
        console_dump(self.dump)

    def test_program_0_by_default(self) -> None:
        out = self.pack("g.nup")
        self.assertIn("program", self.stderr)   # program 1 named, left out
        self.assertEqual(sb_flags(out), 0)
        self.assertEqual(records(out), [(0, APP, 0, 0, 0, 0), (1, APP, V, 0, 0, 0),
                                        (2, ADDON, 0, 0, 0, 0), (3, PATCH, 131072, 0, ADDON, 0)])
        self.assertVerifies(out)

    def test_all_programs(self) -> None:
        out = self.pack("g.nup", all_programs=True)
        self.assertEqual(sb_flags(out), F.SB_FLAG_MULTI_PROGRAM)
        recs = records(out)
        # program 1 arrived with the update: no base of its own, so standalone
        self.assertIn((1, S2, V, 1, 0, F.PKG_FLAG_STANDALONE_UPDATE), recs)
        self.assertIn((1, APP, V, 0, 0, 0), recs)
        self.assertVerifies(out)

    def test_wrong_record_refused(self) -> None:
        put(os.path.join(self.dump, "dlc", f"{ADDON:016x}", "0", "updates", "131072", "program", "0", "meta", "DataPatch.cnmt"),
            cnmt(PATCH, 131072, 0x84, struct.pack("<QQII8x", ADDON + 5, APP, V, 0)))
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            pack(os.path.join(self.tmp, "g.nup"), self.dump)
        self.assertIn("of add-on", str(e.exception))

    def test_split_names_programs(self) -> None:
        out = self.pack("g.nup", all_programs=True, split=True)
        names = sorted(os.listdir(self.tmp))
        self.assertIn("g.update.327680.nup", names)
        self.assertIn("g.update.327680.program1.nup", names)
        self.assertIn("g.dlc.nup", names)
        self.assertEqual(records(os.path.join(self.tmp, "g.dlc.nup"))[1][4], ADDON)
        del out

    def test_edit_keeps_the_new_fields(self) -> None:
        out = self.pack("g.nup", all_programs=True)
        before = records(out)
        edit(out, name="Renamed")
        self.assertEqual(records(out), before)
        self.assertEqual(sb_flags(out), F.SB_FLAG_MULTI_PROGRAM)
        self.assertVerifies(out)


class YoinkLayout(Case):
    def setUp(self) -> None:
        super().setUp()
        yoink_dump(self.dump)

    def test_packages(self) -> None:
        out = self.pack("g.nup", all_programs=True)
        recs = records(out)
        self.assertIn((1, S2, V, 1, 0, F.PKG_FLAG_STANDALONE_UPDATE), recs)
        self.assertIn((3, PATCH, 131072, 0, ADDON, 0), recs)
        with NupReader(out) as r:
            self.assertNotIn(b"romfs.img", [f[0] for f in r.files])
        self.assertVerifies(out)

    def test_without_all_programs(self) -> None:
        out = self.pack("g.nup")
        self.assertIn(os.path.join("switch2", "updates", str(V)), self.stderr)
        self.assertEqual(sb_flags(out), 0)
        self.assertNotIn(S2, [r[1] for r in records(out)])


class OunceCakeTar(Case):
    def test_tar_mode_refused_with_what_to_do(self) -> None:
        put(os.path.join(self.dump, "base", "program", "0", "romfs.tar"), b"tar")
        put(os.path.join(self.dump, "base", "program", "0", "exefs", "main.npdm"), npdm(APP))
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            pack(os.path.join(self.tmp, "g.nup"), self.dump)
        self.assertIn("tar -xf romfs.tar -C romfs", str(e.exception))


class OtherProducers(Case):
    """Files as Yoink (1.0) writes them: kind 3 under the add-on's id with no parent_id, and a Switch 2
    Edition's program as a second update of program 0 with its own id"""
    def setUp(self) -> None:
        super().setUp()
        yoink_dump(self.dump)

    def test_yoink_encoding_opens(self) -> None:
        out = self.pack("g.nup", all_programs=True)

        def as_yoink(i: int, p: F.Package) -> F.Package:
            if p.kind == F.KIND_ADDON_UPDATE:
                p.title_id, p.parent_id, p.flags = p.parent_id, 0, 0
            if p.program_index:
                p.program_index, p.flags = 0, 0   # beside program 0's base: not standalone
            return p
        rewrite(out, as_yoink, sb_flags_value=0)
        with NupReader(out) as r:   # opens: nothing ambiguous
            au = next(p for p in r.packages if p.kind == F.KIND_ADDON_UPDATE)
            self.assertEqual((au.title_id, au.parent_id, au.addon_id), (ADDON, 0, ADDON))
            ups = sorted((p.title_id, p.version) for p in r.packages if p.kind == F.KIND_UPDATE)
            self.assertEqual(ups, [(APP, V), (S2, V)])
        ok, problems = verify(out)
        self.assertFalse(ok)
        self.assertTrue(any("without parent_id" in p for p in problems), problems)


class ReaderRefuses(Case):
    def setUp(self) -> None:
        super().setUp()
        console_dump(self.dump)
        self.out = self.pack("g.nup", all_programs=True)

    def refused(self, change, text: str) -> None:
        rewrite(self.out, change)
        with self.assertRaises(F.NupError) as e:
            NupReader(self.out)
        self.assertIn(text, str(e.exception))

    def test_program_index_on_an_addon(self) -> None:
        self.refused(lambda i, p: (setattr(p, "program_index", 2) if p.kind == F.KIND_ADDON else None) or p,
                     "program_index on a addon")

    def test_two_updates_of_one_program_and_version(self) -> None:
        self.refused(lambda i, p: (setattr(p, "title_id", APP) or setattr(p, "program_index", 0)
                                   if p.kind == F.KIND_UPDATE and p.program_index else None) or p,
                     "second update")

    def test_unknown_superblock_flag(self) -> None:
        with open(self.out, "r+b") as fh:
            fh.seek(0x4C)
            fh.write(struct.pack("<I", 0x3))
        with self.assertRaises(F.NupError) as e:
            NupReader(self.out)
        self.assertIn("unknown superblock flags 0x2", str(e.exception))


if __name__ == "__main__":
    unittest.main()
