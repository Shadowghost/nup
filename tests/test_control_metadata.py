"""Title metadata from the dump's Control content (SPEC §3.1)."""

from pathlib import Path

import pytest

from conftest import BASE_TITLE_ID, make_dump, nup_main
from nup.layout import TlvType, parse_localized
from nup.nacp import LANGUAGES, parse_nacp
from nup.reader import read_container

APP = BASE_TITLE_ID


def nacp(names: dict[str, tuple[str, str]], display_version: str) -> bytes:
    """A 0x4000-byte NACP; ``names`` maps a NACP language to (name, publisher)."""
    data = bytearray(0x4000)
    slots = [language for language, _tag in LANGUAGES]
    for language, (name, publisher) in names.items():
        entry = slots.index(language) * 0x300
        data[entry : entry + len(name.encode())] = name.encode()
        data[entry + 0x200 : entry + 0x200 + len(publisher.encode())] = publisher.encode()
    data[0x3060 : 0x3060 + len(display_version)] = display_version.encode()
    return bytes(data)


def add_control(content: Path, display_version: str, icon_size: int = 0x800, **names) -> None:
    control = content / "control"
    control.mkdir(parents=True, exist_ok=True)
    titles = names or {"AmericanEnglish": ("Wonder Title", "Wonder Pub"),
                       "Japanese": ("ワンダー", "ワンダー社"),
                       "BritishEnglish": ("Wonder Title", "Wonder Pub")}
    (control / "control.nacp").write_bytes(nacp(titles, display_version))
    for language in titles:
        (control / f"icon_{language}.dat").write_bytes(b"\xff\xd8" + language.encode() * (icon_size // 16))


@pytest.fixture
def console_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "Wonder"
    make_dump(root / "base/program/0", title_id=APP, seed=b"base")
    (root / "base/program/0/romfs/a.bin").write_bytes(b"a" * 100)
    add_control(root / "base/program/0", "1.0.0")
    for version, display in ((65536, "1.0.1"), (131072, "1.1.0")):
        content = root / f"updates/{version}/program/0"
        make_dump(content, title_id=APP, seed=str(version).encode())
        (content / "romfs/a.bin").write_bytes(str(version).encode() * 50)
        add_control(content, display)
    return root


def record_of(path: Path, index: int = 0):
    with read_container(str(path)) as reader:
        return reader.meta.packages[index]


def text(record, tlv: TlvType) -> str | None:
    value = record.tlvs.get(tlv)
    return None if value is None else value.decode("utf-8")


def test_parse_nacp_reads_names_publisher_and_display_version() -> None:
    control = parse_nacp(nacp({"Japanese": ("ワンダー", "社"), "French": ("Merveille", "Éd")}, "2.0.1"))
    assert control.names == {"ja": "ワンダー", "fr": "Merveille"}
    assert control.default_language == "ja"  # first named slot in NACP order
    assert control.publishers["fr"] == "Éd"
    assert control.display_version == "2.0.1"


def test_bundle_takes_each_package_metadata_from_its_own_nacp(
        console_bundle: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert nup_main(["pack", str(console_bundle)]) == 0
    base = record_of(tmp_path / "Wonder.nup")
    assert text(base, TlvType.TITLE_NAME) == "Wonder Title"
    assert text(base, TlvType.PUBLISHER) == "Wonder Pub"
    assert text(base, TlvType.DISPLAY_VERSION) == "1.0.0"
    # en-GB repeats the default name, so only Japanese is localized
    localized = parse_localized(memoryview(base.tlvs[TlvType.LOCALIZED_TITLE]))
    assert localized == {"ja": "ワンダー".encode()}
    icon = console_bundle / "base/program/0/control/icon_AmericanEnglish.dat"
    assert base.tlvs[TlvType.ICON] == icon.read_bytes()
    for version, display in ((65536, "1.0.1"), (131072, "1.1.0")):
        update = record_of(tmp_path / f"Wonder.update.{version}.nup")
        assert text(update, TlvType.DISPLAY_VERSION) == display
        # identity TLVs live on the base record (SPEC §3)
        assert {TlvType.TITLE_NAME, TlvType.ICON, TlvType.LOCALIZED_TITLE}.isdisjoint(update.tlvs)


def test_flags_override_the_nacp(console_bundle: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert nup_main(["pack", str(console_bundle), "--name", "Renamed",
                     "--display-version", "9.9.9"]) == 0
    base = record_of(tmp_path / "Renamed.nup")
    assert text(base, TlvType.TITLE_NAME) == "Renamed"
    assert text(base, TlvType.PUBLISHER) == "Wonder Pub"
    # the display version names the newest package, the version dumped; the others keep their own
    assert text(base, TlvType.DISPLAY_VERSION) == "1.0.0"
    assert text(record_of(tmp_path / "Renamed.update.65536.nup"), TlvType.DISPLAY_VERSION) == "1.0.1"
    assert text(record_of(tmp_path / "Renamed.update.131072.nup"), TlvType.DISPLAY_VERSION) == "9.9.9"


def test_display_version_flag_names_the_update_of_a_dump_without_control(
        tmp_path: Path, monkeypatch) -> None:
    """A Yoink bundle has no control/: the flag goes on the update, not the base, in a
    bundle pack and in a standalone one."""
    root = tmp_path / "Yoink"
    make_dump(root / "base", title_id=APP, seed=b"b")
    make_dump(root / "updates/65536", title_id=APP, seed=b"u")
    monkeypatch.chdir(tmp_path)
    assert nup_main(["pack", str(root), "--display-version", "1.2.0"]) == 0
    assert TlvType.DISPLAY_VERSION not in record_of(tmp_path / "base.nup").tlvs
    assert text(record_of(tmp_path / "65536.update.65536.nup"), TlvType.DISPLAY_VERSION) == "1.2.0"
    out = tmp_path / "standalone.nup"
    assert nup_main(["pack", str(root), "--kind", "standalone", "--display-version", "1.2.0",
                     "-o", str(out), "--title-id", f"{APP:x}"]) == 0
    with read_container(str(out)) as reader:
        base, update = reader.meta.packages
    assert TlvType.DISPLAY_VERSION not in base.tlvs
    assert text(update, TlvType.DISPLAY_VERSION) == "1.2.0"


def test_lang_name_flag_merges_with_the_nacp_names(tmp_path: Path) -> None:
    """--lang-name sets its own language: the Control content's other names stay."""
    content = make_dump(tmp_path / "named", title_id=APP, seed=b"n")
    add_control(content, "1.0.0")
    out = tmp_path / "named.nup"
    assert nup_main(["pack", str(content), "-o", str(out), "--lang-name", "ko=원더"]) == 0
    localized = parse_localized(memoryview(record_of(out).tlvs[TlvType.LOCALIZED_TITLE]))
    assert localized == {"ja": "ワンダー".encode(), "ko": "원더".encode()}


def test_update_dump_alone_takes_the_full_set(tmp_path: Path) -> None:
    content = make_dump(tmp_path / "upd", title_id=APP, seed=b"u")
    (content / "romfs/a.bin").write_bytes(b"a")
    add_control(content, "3.2.0")
    out = tmp_path / "out.nup"
    assert nup_main(["pack", str(content), "-o", str(out)]) == 0
    record = record_of(out)
    assert text(record, TlvType.TITLE_NAME) == "Wonder Title"
    assert text(record, TlvType.DISPLAY_VERSION) == "3.2.0"


def test_one_display_version_flag_cannot_name_several_updates(tmp_path: Path, capsys) -> None:
    root = tmp_path / "OnlyUpdates"
    for version in (65536, 131072):
        make_dump(root / f"updates/{version}", title_id=APP, seed=str(version).encode())
        (root / f"updates/{version}/romfs/a.bin").write_bytes(b"a")
    assert nup_main(["pack", str(root), "--display-version", "1.0.0"]) != 0
    assert "--display-version names one package" in capsys.readouterr().err
    # nor can one -o file hold them: each would overwrite the one before
    assert nup_main(["pack", str(root), "-o", str(tmp_path / "one.nup")]) != 0
    assert "-o names one file" in capsys.readouterr().err
    assert not (tmp_path / "one.nup").exists()


def test_parse_nacp_reads_a_compressed_title_area() -> None:
    """Newer SDKs deflate the title entries (Super Mario Bros. Wonder 1.2.1): u16 size + raw deflate."""
    import struct, zlib
    plain = nacp({"AmericanEnglish": ("Wonder Title", "Wonder Pub")}, "1.2.1")
    titles = plain[: 16 * 0x300] + bytes(16 * 0x300)   # 32 entries when inflated
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = packer.compress(titles) + packer.flush()
    data = bytearray(plain)
    data[: 16 * 0x300] = bytes(16 * 0x300)   # in place of the 16 plain entries
    data[: 2 + len(packed)] = struct.pack("<H", len(packed)) + packed
    control = parse_nacp(bytes(data))
    assert control.names == {"en-US": "Wonder Title"} and control.publishers["en-US"] == "Wonder Pub"
    assert control.display_version == "1.2.1"


def test_oversized_icon_is_reencoded_to_fit(tmp_path: Path) -> None:
    """Console icons are often 150-200 KB: with Pillow the packer stores a 256x256 JPEG that fits (SPEC §3.1)."""
    pil = pytest.importorskip("PIL.Image")
    import io, os
    content = make_dump(tmp_path / "big", title_id=APP, seed=b"b")
    add_control(content, "1.0.0")
    noise = pil.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3))   # random pixels: a JPEG far over 64 KiB
    big = io.BytesIO(); noise.save(big, format="JPEG", quality=100)
    assert big.tell() > 0x10000
    for icon in (content / "control").glob("icon_*.dat"):
        icon.write_bytes(big.getvalue())
    out = tmp_path / "big.nup"
    assert nup_main(["pack", str(content), "-o", str(out)]) == 0
    icon = record_of(out).tlvs[TlvType.ICON]
    assert icon[:3] == b"\xff\xd8\xff" and len(icon) < 0x10000
    assert pil.open(io.BytesIO(icon)).size == (256, 256)


def test_icon_left_out_when_it_does_not_fit(tmp_path: Path) -> None:
    """An oversized icon that is no image Pillow can read is left out (the name still comes from the NACP)."""
    content = make_dump(tmp_path / "big", title_id=APP, seed=b"b")
    add_control(content, "1.0.0", icon_size=0x12000)
    out = tmp_path / "big.nup"
    assert nup_main(["pack", str(content), "-o", str(out)]) == 0
    record = record_of(out)
    assert TlvType.ICON not in record.tlvs
    assert text(record, TlvType.TITLE_NAME) == "Wonder Title"


def test_base_without_control_takes_the_newest_updates_identity(
        console_bundle: Path, tmp_path: Path, monkeypatch) -> None:
    """A dump of an installed game: the update's Control replaced the base's (SPEC §3.1)."""
    import shutil
    shutil.rmtree(console_bundle / "base/program/0/control")
    add_control(console_bundle / "updates/65536/program/0", "1.0.1", AmericanEnglish=("Old Name", "Old Pub"))
    monkeypatch.chdir(tmp_path)
    assert nup_main(["pack", str(console_bundle)]) == 0
    base = record_of(tmp_path / "Wonder.nup")
    assert text(base, TlvType.TITLE_NAME) == "Wonder Title" and text(base, TlvType.PUBLISHER) == "Wonder Pub"   # 131072's, the newest
    icon = console_bundle / "updates/131072/program/0/control/icon_AmericanEnglish.dat"
    assert base.tlvs[TlvType.ICON] == icon.read_bytes()
    assert TlvType.DISPLAY_VERSION not in base.tlvs   # the update's version is not the base's
    assert text(record_of(tmp_path / "Wonder.update.131072.nup"), TlvType.DISPLAY_VERSION) == "1.1.0"


def test_dump_without_control_keeps_working(base_dump: Path, tmp_path: Path) -> None:
    out = tmp_path / "plain.nup"
    assert nup_main(["pack", str(base_dump), "-o", str(out)]) == 0
    assert TlvType.TITLE_NAME not in record_of(out).tlvs
