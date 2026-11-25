# kl5core

Building blocks for a payphone manager: the program a payphone calls to
report in and be programmed. Pure Python, standard library only, no I/O:
bytes in, bytes out.

## What is here

| Module | What it is |
|---|---|
| `xnetproto` | The Protel XNet link as it appears on the wire: framing and checksums, a decoder for every packet type seen, the call-in, call records (CDR) and the call-record ring, the board's clock, a reader for relay captures, and `ReplayManager`, a state machine that answers a call-in by replaying the manager's side of a recorded session |
| `protel_firmware_xnet` | Memory-write and read-answer frame details for the Protel 4000/8000 (DD8822) |
| `millennium` | Nortel Millennium tables for a download: CARD, RATE, call screening, speed dials, and which a given terminal (MTR 1.x or 2.x) takes |

The Millennium tables are the files [mm_manager](https://github.com/hharte/mm_manager)
sends (`mm_table_XX.bin`). Parsing and re-rendering every one of its
shipped default tables gives back the same bytes, and the tests check that.
Our fork of mm_manager, with the fixes we run in production, is
[kl5networks/mm_manager](https://github.com/kl5networks/mm_manager).

## What is not here

The Protel side stops at the protocol. Building a full Protel download
(the options, rates and zone tables) is not included. The modems these
phones call in with are in
[kl5networks/kl5-modems](https://github.com/kl5networks/kl5-modems).

## How it was worked out

The XNet protocol was read off the line from our own Protel board's
call-ins. No manual publishes it. Some details were confirmed by running
the phone's firmware in an emulator; that emulator and the firmware are
not part of this repository. Fields whose meaning is not known are kept as
raw bytes rather than guessed at.

## Tests

    python3 -m unittest discover -s tests -t .

## Notice

Not affiliated with Protel, Nortel or any payphone maker. No firmware,
ROM image or manufacturer software is included. For phones you own.

## Licence and credits

MIT, Copyright (c) 2026 White Clay Technologies LLC. See `LICENSE`.
The mm_manager tables under `tests/fixtures/mm_manager` are Howard M.
Harte's, MIT, with their own `LICENSE`.
Written by John Walker for White Clay Technologies LLC, part of KL5 Networks.

With thanks to Howard M. Harte and the mm_manager contributors; to the US
Payphones wiki ([payphones.telesfor.org](https://payphones.telesfor.org/),
Piotr Czerniak, with PhilTel and danc256) and to muCCC
([wiki.muc.ccc.de](https://wiki.muc.ccc.de/millennium:start)) for what is
known about the Millennium; and to the participants in the Millennium and
Protel channels on the PhilTel Discord.
