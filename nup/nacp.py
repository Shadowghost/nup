"""Title metadata from a dump's Control content (SPEC §3.1).

The console keeps a title's names, publisher, display version and icons in
its Control content: ``control.nacp`` (the application control property,
switchbrew "NACP") plus one ``icon_<Language>.dat`` JPEG per language.
OunceCake serves that content as ``control/`` beside ``exefs/`` and
``romfs/``; Yoink's FTP tree has none, so its dumps keep relying on flags.
"""

import os
from dataclasses import dataclass, field

from .layout import TLV_MAX_LENGTH, TlvType, encode_localized

TITLE_ENTRY_SIZE = 0x300
TITLE_NAME_SIZE = 0x200
TITLE_PUBLISHER_SIZE = 0x100
DISPLAY_VERSION_OFFSET = 0x3060
DISPLAY_VERSION_SIZE = 0x10
NACP_MIN_SIZE = DISPLAY_VERSION_OFFSET + DISPLAY_VERSION_SIZE
TLV_HEADER_SIZE = 4

# NACP title slots in order, the console's icon file name for each, and the
# BCP 47 tag the localized TLVs carry (SPEC §3.1).
LANGUAGES = (
    ("AmericanEnglish", "en-US"),
    ("BritishEnglish", "en-GB"),
    ("Japanese", "ja"),
    ("French", "fr"),
    ("German", "de"),
    ("LatinAmericanSpanish", "es-419"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("Dutch", "nl"),
    ("CanadianFrench", "fr-CA"),
    ("Portuguese", "pt"),
    ("Russian", "ru"),
    ("Korean", "ko"),
    ("TraditionalChinese", "zh-Hant"),
    ("SimplifiedChinese", "zh-Hans"),
    ("BrazilianPortuguese", "pt-BR"),
)
# older tools name the two Chinese icons after the region
ICON_ALIASES = {"Taiwanese": "TraditionalChinese", "Chinese": "SimplifiedChinese"}


@dataclass(slots=True)
class ControlData:
    names: dict[str, str] = field(default_factory=dict)  # BCP 47 tag -> name, NACP slot order
    publishers: dict[str, str] = field(default_factory=dict)
    display_version: str | None = None
    icons: dict[str, str] = field(default_factory=dict)  # BCP 47 tag -> icon file path

    @property
    def default_language(self) -> str | None:
        """The first language with a name, as the console falls back."""
        return next(iter(self.names), None)


def find_control_dir(content_dir: str) -> str | None:
    """The folder holding control.nacp: <content>/control/, or the program
    index folder's control/ when the content is program/<index>/program/."""
    candidates = [os.path.join(content_dir, "control"), content_dir]
    if os.path.basename(os.path.normpath(content_dir)) == "program":
        candidates.insert(1, os.path.join(os.path.dirname(os.path.normpath(content_dir)), "control"))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "control.nacp")):
            return candidate
    return None


def read_control(content_dir: str) -> ControlData | None:
    control_dir = find_control_dir(content_dir)
    if control_dir is None:
        return None
    with open(os.path.join(control_dir, "control.nacp"), "rb") as handle:
        data = handle.read(NACP_MIN_SIZE)
    if len(data) < NACP_MIN_SIZE:
        return None  # not a NACP; the flags still apply
    control = parse_nacp(data)
    control.icons = find_icons(control_dir)
    return control


TITLE_SLOTS = 32   # the title area's entries (16 named languages, then reserved)


def title_area(data: bytes) -> bytes:
    """The NACP's title entries. Newer SDKs store them compressed (Super Mario
    Bros. Wonder 1.2.1): a u16 size, then raw deflate inflating to 32 entries
    of 0x300 bytes. Inflated when its deflate stream ends exactly there (no
    further than 32 entries, whatever the size field says); else read as is
    (plain names: deflate output can decode as text, so the stream decides)."""
    import struct, zlib
    want = TITLE_SLOTS * TITLE_ENTRY_SIZE
    try:
        size = struct.unpack_from("<H", data)[0]
        inflater = zlib.decompressobj(-15)
        out = inflater.decompress(data[2 : 2 + size], want)
        if inflater.eof and len(out) == want:
            return out
    except (struct.error, zlib.error):
        pass
    return data[:want]


def parse_nacp(data: bytes) -> ControlData:
    control = ControlData()
    titles = title_area(data)
    for slot, (_language, tag) in enumerate(LANGUAGES):
        entry = slot * TITLE_ENTRY_SIZE
        name = c_string(titles[entry : entry + TITLE_NAME_SIZE])
        if name:
            control.names[tag] = name
            publisher = c_string(titles[entry + TITLE_NAME_SIZE : entry + TITLE_ENTRY_SIZE])
            if publisher:
                control.publishers[tag] = publisher
    control.display_version = c_string(
        data[DISPLAY_VERSION_OFFSET : DISPLAY_VERSION_OFFSET + DISPLAY_VERSION_SIZE]
    )
    return control


def c_string(raw: bytes) -> str | None:
    """A NUL-terminated UTF-8 field; empty or undecodable reads as absent."""
    try:
        text = raw.split(b"\0", 1)[0].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return text or None


def find_icons(control_dir: str) -> dict[str, str]:
    tags = dict(LANGUAGES)
    icons = {}
    for name in sorted(os.listdir(control_dir)):
        if not (name.startswith("icon_") and name.endswith(".dat")):
            continue
        language = name[len("icon_") : -len(".dat")]
        tag = tags.get(ICON_ALIASES.get(language, language))
        path = os.path.join(control_dir, name)
        if tag is not None and tag not in icons and os.path.isfile(path):
            icons[tag] = path
    return icons


def control_tlvs(control: ControlData, *, identity: bool) -> dict[int, bytes]:
    """Text TLVs from a NACP.  ``identity`` adds name, publisher and the
    localized names; without it only the display version (an update's
    record, SPEC §3)."""
    tlvs: dict[int, bytes] = {}
    if control.display_version:
        tlvs[TlvType.DISPLAY_VERSION] = control.display_version.encode("utf-8")
    default = control.default_language
    if not identity or default is None:
        return tlvs
    tlvs[TlvType.TITLE_NAME] = control.names[default].encode("utf-8")
    publisher = control.publishers.get(default) or next(iter(control.publishers.values()), None)
    if publisher:
        tlvs[TlvType.PUBLISHER] = publisher.encode("utf-8")
    # a language whose name equals the default adds nothing: readers fall back to it
    localized = {
        tag: name.encode("utf-8") for tag, name in control.names.items()
        if name != control.names[default]
    }
    if localized:
        tlvs[TlvType.LOCALIZED_TITLE] = encode_localized(localized)
    return tlvs


def control_icon(control: ControlData, tlvs: dict[int, bytes]) -> bytes | None:
    """The default language's icon in what is left of the 64 KiB TLV block
    (SPEC §3.1, §10.3): as it is when it fits, else re-encoded as a smaller
    256x256 JPEG (image.prepare_icon, with Pillow); None when it cannot be
    made to fit (no Pillow, an image Pillow cannot read, no room left)."""
    from .errors import NupError
    from .image import prepare_icon
    default = control.default_language
    path = control.icons.get(default) if default else None
    if path is None:
        path = next(iter(control.icons.values()), None)
    if path is None:
        return None
    used = sum(TLV_HEADER_SIZE + len(value) for value in tlvs.values())
    budget = TLV_MAX_LENGTH - used - TLV_HEADER_SIZE
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) <= budget:
        return data   # the console's file as it is
    try:
        return prepare_icon(data, budget, source=os.path.basename(path))
    except NupError:
        return None
