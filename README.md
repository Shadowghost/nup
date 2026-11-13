# NUP v2

A package for decrypted NX (Nintendo Switch 1/2) dumps, derived
from the NSP layout with the encryption and signatures removed.

Instead of using sigs and encryption it uses hashes that can be 
checked and verifed before being run by an emulator.

## Goal

Take the NSP structure - partition filesystem holding content files plus
content metadata - and strip everything that requires keys:

- no NCA-level crypto (XTS header, key areas, CTR/BKTR sections)
- no RSA signatures (fixed-key, NPDM, ticket/cert chain)
- content hashes kept instead, so an emulator can verify data integrity
  and validity later

Extra things that will exist in v2:

- update dumps for games will be diffed and only the diff stored
 so it can be applied to the base (this might be an issue with games
 we dont have the base of, in those cases, store update as base(?))

## Status

Designing.  A reference implementation lives in `nup/`.

## Tool

```sh
pip install .            # provides the `nup` command

nup pack DKB-base -o DonkeyKongBananza.nup --name "Donkey Kong Bananza" \
    --display-version 1.0.0
nup pack DKB-update --base DonkeyKongBananza.nup \
    -o DonkeyKongBananza.update.458752.nup --version 458752 --display-version 3.2.0
nup info  DonkeyKongBananza.nup [--json] [--entries]
nup verify DonkeyKongBananza.nup --level bind|package|full
nup extract DonkeyKongBananza.update.458752.nup --base DonkeyKongBananza.nup \
    --apply -o game-tree/
nup edit  game.nup --set-tlv name=Renamed --add-file base/romfs/x.bin=./x.bin
```

`pack` accepts a content directory (`exefs/` + `romfs/`), a dump bundle
(`base/`, `updates/<version>/`, `dlc/`), or an update-only dump, which is
stored as a base (SPEC §5.2).  `--kind standalone` packs base + one update
together (§5.3); `--chain` packs a diff against already-packed updates (§5.1
chains).  Tests: `python3 -m pytest` (synthetic + real-dump subsets);
`pytest -m real` packs the full dumps.

Bundles are read in the dump tools' layouts: flattened, the console's as
OunceCake serves it (`base/program/<index>/`, `updates/<v>/program/<index>/`,
`dlc/<id>/<v>/base/` and `.../updates/<v>/`), and Yoink's (`switch2/updates/<v>/`,
`dlc/<data patch id>/<v>/`).  OunceCake's `--tar` output must be unpacked
first; a `romfs.img` beside `romfs/` is skipped.  A multi-program title's
other programs pack into `<name>.program<N>...` files (§4.1); `--kind dlc` writes the add-ons' data patches to `<name>.dlc.update.nup`
(§5.4), materialized with `nup extract <name>.dlc.update.nup --base <name>.dlc.nup
--apply`.

## v1

The v1 format SPEC (own container, per-file zstd, TOC hashes) and its 
Python reference implementation are on the `meow` branch.
