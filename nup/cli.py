"""Command line interface: pack, info, verify, extract, edit."""

import argparse
import json
import os
import sys

from . import __version__
from .errors import NupError
from .packcli import add_pack_arguments, command_pack
from .reader import NupReader, read_container


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    container = getattr(args, "file", None)
    if container is not None and os.path.isdir(container):
        hint = "; use `nup pack` to turn a dump folder into a .nup" if args.command == "info" else ""
        print(f"nup: error: {container!r} is a folder, expected a .nup file{hint}", file=sys.stderr)
        return 1
    try:
        return args.handler(args)
    except NupError as error:
        print(f"nup: error: {error}", file=sys.stderr)
        return 1
    except OSError as error:  # a missing or unreadable file: one line, not a traceback (PR #22)
        if error.filename and os.path.isdir(error.filename):   # a folder as --base, --chain, ...
            print(f"nup: error: {error.filename!r} is a folder, expected a .nup file", file=sys.stderr)
        else:
            print(f"nup: error: {error}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nup", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    pack = sub.add_parser("pack", help="pack a dump directory into a .nup container")
    add_pack_arguments(pack)
    pack.set_defaults(handler=command_pack)

    info = sub.add_parser("info", help="describe a container")
    info.add_argument("file")
    info.add_argument("--json", action="store_true")
    info.add_argument("--entries", action="store_true")
    info.set_defaults(handler=command_info)

    verify = sub.add_parser("verify", help="check container integrity")
    verify.add_argument("file")
    verify.add_argument("--level", choices=("bind", "package", "full"), default="bind")
    verify.add_argument("--package", default=None, help="package scope for --level package")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(handler=command_verify)

    extract = sub.add_parser("extract", help="write container entries to a directory")
    add_extract_arguments(extract)
    extract.set_defaults(handler=command_extract)

    edit = sub.add_parser("edit", help="modify a container")
    add_edit_arguments(edit)
    edit.set_defaults(handler=command_edit)
    return parser


def add_extract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--apply", action="store_true", help="materialize the game tree")
    parser.add_argument("--base", help="base .nup when materializing an update container")
    parser.add_argument("--chain", action="append", default=[], metavar="UPDATE_NUP",
                        help="intermediate update container(s) to apply first, in version order")
    parser.add_argument("--package", default=None, help="extract one package scope only")
    parser.add_argument("--no-verify", action="store_true", help="skip per-file hash checks")
    parser.add_argument("--json", action="store_true")


def add_edit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file")
    parser.add_argument("-o", "--out", help="output path (default: edit in place)")
    parser.add_argument("--package", default="base", help="package for TLV edits")
    parser.add_argument("--set-tlv", action="append", default=[], metavar="TYPE=VALUE")
    parser.add_argument("--del-tlv", action="append", default=[], metavar="TYPE")
    parser.add_argument("--add-file", action="append", default=[], metavar="PATH=SRC")
    parser.add_argument("--replace-file", action="append", default=[], metavar="PATH=SRC")
    parser.add_argument("--rm-file", action="append", default=[], metavar="PATH")
    parser.add_argument("--base", help="base .nup for update identity recomputation")


def command_info(args) -> int:
    from .info import container_info, render_info

    with NupReader(args.file) as reader:
        info = container_info(reader, include_entries=args.entries)
    print(json.dumps(info, indent=2) if args.json else render_info(info))
    return 0


def command_verify(args) -> int:
    from .verify import verify_container

    with NupReader(args.file) as reader:
        report = verify_container(reader, args.level, args.package)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(
            f"{args.file}: {report.level} verification passed "
            f"({report.entries_hashed} entries, {report.bytes_hashed:,} bytes hashed)"
        )
    return 0


def command_extract(args) -> int:
    from .extract import extract_with_options, summarize

    base_reader = read_container(args.base) if args.base else None
    chain = [read_container(link) for link in args.chain]
    try:
        with NupReader(args.file) as reader:
            report = extract_with_options(
                reader,
                args.out,
                package=args.package,
                apply=args.apply,
                base_reader=base_reader,
                chain=chain,
                verify=not args.no_verify,
            )
    finally:
        for link in chain:
            link.close()
        if base_reader is not None:
            base_reader.close()
    print(summarize(report))
    return 0


def command_edit(args) -> int:
    from .edit import edit_container, edit_in_place, ops_from_args

    ops = ops_from_args(args)
    report = edit_container(args.file, args.out, ops) if args.out else edit_in_place(args.file, ops)
    for line in report.warnings:
        print(f"nup: warning: {line}", file=sys.stderr)
    print(
        f"edited {report.output}: {report.tlvs_set} TLVs set, {report.tlvs_removed} removed, "
        f"{report.files_added} added, {report.files_replaced} replaced, {report.files_removed} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
