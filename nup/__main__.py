"""`python -m nup`: the same CLI as the `nup` entry point, exit status included."""

from .cli import main

raise SystemExit(main())
