"""Container editing: TLVs and entries, with hash chains kept consistent."""

import pytest

from conftest import BASE_TITLE_ID, pack_base
from nup.edit import EditOps, edit_container
from nup.errors import NupError
from nup.layout import TlvType
from nup.reader import read_container


def test_set_and_delete_tlvs(base_nup, tmp_path):
    out = tmp_path / "edited.nup"
    report = edit_container(
        str(base_nup),
        str(out),
        EditOps(set_tlvs={int(TlvType.TITLE_NAME): b"Renamed", int(0x1234): b"future"}),
    )
    assert report.tlvs_set == 2
    with read_container(str(out)) as reader:
        record = reader.meta.packages[0]
        assert record.tlvs[TlvType.TITLE_NAME] == b"Renamed"
        assert record.tlvs[0x1234] == b"future"
        assert record.tlvs[TlvType.PUBLISHER] == b"Test Pub"


def test_unknown_tlvs_survive_another_edit(base_dump, tmp_path):
    """Editors MUST preserve unknown TLV types; readers MUST ignore them (SPEC §3)."""
    source = tmp_path / "a.nup"
    pack_base(base_dump, source, title_id=f"{BASE_TITLE_ID:x}", name="X")
    once = tmp_path / "once.nup"
    edit_container(str(source), str(once), EditOps(set_tlvs={0x0BEE: b"mystery"}))
    twice = tmp_path / "twice.nup"
    edit_container(str(once), str(twice), EditOps(set_tlvs={int(TlvType.PUBLISHER): b"Pub"}))
    with read_container(str(twice)) as reader:
        tlvs = reader.meta.packages[0].tlvs
    assert tlvs[0x0BEE] == b"mystery"
    assert tlvs[TlvType.PUBLISHER] == b"Pub"


def test_add_replace_remove_files(base_nup, tmp_path):
    new_file = tmp_path / "new.bin"
    new_file.write_bytes(b"brand new bytes")
    out = tmp_path / "edited.nup"
    edit_container(
        str(base_nup),
        str(out),
        EditOps(
            add_files={"base/romfs/new.bin": str(new_file)},
            remove_files={"base/romfs/todelete.bin"},
        ),
    )
    with read_container(str(out)) as reader:
        assert reader.read_file("base/romfs/new.bin") == b"brand new bytes"
        assert "base/romfs/todelete.bin" not in [
            reader.entry_name_of(record, offset)
            for record in reader.meta.packages
            for offset in range(len(record.files))
        ]
        verify_content_hash(reader, 0)


def verify_content_hash(reader, index):
    from nup.layout import content_hash

    record = reader.meta.packages[index]
    assert record.content_hash == content_hash(record.files)


def test_replace_file_rehashes_entry(base_nup, tmp_path):
    replacement = tmp_path / "replacement.bin"
    payload = b"replacement payload"
    replacement.write_bytes(payload)
    out = tmp_path / "edited.nup"
    edit_container(
        str(base_nup),
        str(out),
        EditOps(replace_files={"base/romfs/small.bin": str(replacement)}),
    )
    with read_container(str(out)) as reader:
        assert reader.read_file("base/romfs/small.bin") == payload
        verify_content_hash(reader, 0)


def test_editing_requires_an_existing_scope(base_nup, tmp_path):
    out = tmp_path / "edited.nup"
    with pytest.raises(NupError):
        edit_container(
            str(base_nup),
            str(out),
            EditOps(add_files={"brand/new/path.bin": "/dev/null"}),
        )


def test_in_place_edit(base_dump, tmp_path):
    source = pack_base(base_dump, tmp_path / "live.nup",
                       title_id=f"{BASE_TITLE_ID:x}", name="Live")
    from nup.edit import edit_in_place

    report = edit_in_place(str(source), EditOps(set_tlvs={int(TlvType.TITLE_NAME): b"InPlace"}))
    assert report.output == str(source)
    with read_container(str(source)) as reader:
        assert reader.meta.packages[0].tlvs[TlvType.TITLE_NAME] == b"InPlace"


@pytest.mark.parametrize("fixture", ["base_nup", "update_nup"])
def test_metadata_patch_matches_a_full_rewrite(fixture, request, tmp_path):
    """A TLV edit that fits meta.nup's padding patches the head only, and the
    file comes out byte for byte as a rewrite through edit_container."""
    import shutil
    from nup.edit import patch_metadata_in_place
    from nup.verify import verify_container

    source = request.getfixturevalue(fixture)
    ops = EditOps(set_tlvs={int(TlvType.DISPLAY_VERSION): b"1.0.0"}, package_selector="0")
    rewritten = tmp_path / "rewritten.nup"
    edit_container(str(source), str(rewritten), ops)
    patched = tmp_path / "patched.nup"
    shutil.copyfile(source, patched)
    report = patch_metadata_in_place(str(patched), ops)
    assert report is not None and report.tlvs_set == 1
    assert patched.read_bytes() == rewritten.read_bytes()
    with read_container(str(patched)) as reader:
        verify_container(reader, "full")


def test_a_bases_version_is_patched_in_place(base_nup, tmp_path):
    """A full dump at a later revision stored as version 0: its version set
    in place, as a rewrite would set it, its identity untouched."""
    import shutil
    from nup.edit import patch_metadata_in_place

    ops = EditOps(version=917504)
    rewritten = tmp_path / "rewritten.nup"
    edit_container(str(base_nup), str(rewritten), ops)
    patched = tmp_path / "patched.nup"
    shutil.copyfile(base_nup, patched)
    report = patch_metadata_in_place(str(patched), ops)
    assert report is not None and report.version_set == 917504
    assert patched.read_bytes() == rewritten.read_bytes()
    with read_container(str(base_nup)) as before, read_container(str(patched)) as after:
        assert after.meta.packages[0].version == 917504
        assert after.meta.packages[0].content_hash == before.meta.packages[0].content_hash


def test_an_updates_version_is_refused(update_nup, tmp_path):
    with pytest.raises(NupError, match="repack it"):
        edit_container(str(update_nup), str(tmp_path / "x.nup"), EditOps(version=1 << 16))


def test_metadata_patch_declines_when_the_layout_moves(base_nup, tmp_path):
    """meta.nup growing past its padding shifts every entry: the in-place edit
    falls back to the full rewrite."""
    import shutil
    from nup.edit import edit_in_place, patch_metadata_in_place

    ops = EditOps(set_tlvs={int(TlvType.PUBLISHER): b"P" * 0x1000})
    copy = tmp_path / "copy.nup"
    shutil.copyfile(base_nup, copy)
    assert patch_metadata_in_place(str(copy), ops) is None
    assert copy.read_bytes() == base_nup.read_bytes()
    rewritten = tmp_path / "rewritten.nup"
    edit_container(str(base_nup), str(rewritten), ops)
    edit_in_place(str(copy), ops)
    assert copy.read_bytes() == rewritten.read_bytes()


def test_edited_container_verifies_full(base_nup, tmp_path):
    from nup.verify import verify_container

    out = tmp_path / "edited.nup"
    edit_container(str(base_nup), str(out), EditOps(set_tlvs={int(TlvType.DISPLAY_VERSION): b"9.9.9"}))
    with read_container(str(out)) as reader:
        verify_container(reader, "full")


def test_update_tlv_edit_keeps_the_materialized_identity(update_nup, tmp_path):
    from nup.verify import verify_container

    out = tmp_path / "upd-edited.nup"
    report = edit_container(
        str(update_nup), str(out), EditOps(package_selector="update", set_tlvs={int(TlvType.DISPLAY_VERSION): b"4.0.0"})
    )
    assert not report.warnings  # no file changed: no --base needed
    with read_container(str(update_nup)) as before, read_container(str(out)) as reader:
        assert reader.meta.packages[0].tlvs[TlvType.DISPLAY_VERSION] == b"4.0.0"
        assert reader.meta.packages[0].content_hash == before.meta.packages[0].content_hash
        verify_container(reader, "full")


def test_update_file_edit_without_base_warns(update_nup, tmp_path):
    extra = tmp_path / "extra.bin"
    extra.write_bytes(b"extra")
    with read_container(str(update_nup)) as reader:
        version = reader.meta.packages[0].version
    report = edit_container(str(update_nup), str(tmp_path / "upd-edited.nup"),
                            EditOps(package_selector="update",
                                    add_files={f"update/{version}/romfs/added-by-edit.bin": str(extra)}))
    assert report.warnings
