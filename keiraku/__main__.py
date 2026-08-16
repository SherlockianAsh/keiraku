"""Allow `python -m keiraku ...` as well as `python -m keiraku.cli ...`.

`python -m keiraku` is what people reach for first. Without this module it
fails with "cannot be directly executed", which is a confusing first contact
with a tool whose whole job is to be easy to attach.
"""

from .cli import main

raise SystemExit(main())
