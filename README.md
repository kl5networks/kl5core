# kl5core

**By John Walker, for [KL5 Networks](https://kl5networks.com)** (White Clay Technologies LLC).

Building blocks for a payphone manager: the program a payphone calls to
report in and be programmed. Python, standard library only. Everything
but `kl5net` is pure, bytes in and bytes out; `kl5net` is the one module
that opens a socket.

## Why KL5 exists

KL5 is a hobby project, made for fun. Payphones like the Protel and the
Nortel Millennium are great machines, and most of the ones that survive sit
in garages collecting dust because the network they were built for is gone.
KL5 is a way to get them working again: making calls, taking coins, calling
in to a manager and being programmed, on an ordinary VoIP line.

The hope is to bring more people in to set up and use their own phones, and
to make that easy. A phone that calls in needs something to answer it, so
the project grew a management console, and the console grew a fictional
telephone company to run it: KL5 Networks, at
[kl5networks.com](https://kl5networks.com). The company is make-believe.
The phones, the call-ins and this code are real.

## What is here

| Module | What it is |
|---|---|
| `xnetproto` | The Protel XNet link as it appears on the wire: framing and checksums, a decoder for every packet type seen, the call-in, call records (CDR) and the call-record ring, the board's clock, a reader for relay captures, and `ReplayManager`, a state machine that answers a call-in by replaying the manager's side of a recorded session |
| `protel_firmware_xnet` | Memory-write and read-answer frame details for the Protel 4000/8000 (DD8822) |
| `protelimage` | Builds a whole Protel download (the memory writes and their checks) from an options record and a costing record |
| `xnetopts` | The options-and-registers record: every keypad register by number, the codes, polling and reporting flags |
| `xnetcost` | The call-costing record: rate bands, restricted numbers, speed dials |
| `protelzones` | The NPA/NXX zone tables: which exchange falls in which cost band |
| `costtemplates` | Ready-made rate plans to start a costing record from |
| `kl5net` | KL5-Net, a Protel manager: an asyncio listener that answers a call-in from a softmodem, runs a download script against the board and writes every byte as a capture |
| `millennium` | Nortel Millennium tables for a download: CARD, RATE, call screening, speed dials, and which a given terminal (MTR 1.x or 2.x) takes |

The Millennium tables are the files [mm_manager](https://github.com/hharte/mm_manager)
sends (`mm_table_XX.bin`). Parsing and re-rendering every one of its
shipped default tables gives back the same bytes, and the tests check that.
Our fork of mm_manager, with the fixes we run in production, is
[kl5networks/mm_manager](https://github.com/kl5networks/mm_manager).

## What is not here

Nothing of ExpressNet, Protel's own manager: no program, no data files,
no captures. The options and costing records use ExpressNet's record
layout, because that is what the download is built from, but this code
reads and writes them only as bytes you supply. The modems these phones
call in with are in
[kl5networks/kl5-modems](https://github.com/kl5networks/kl5-modems).

## How far the Protel builder is proven

A download built from records ExpressNet had saved reproduced a real
ExpressNet download almost byte for byte.
Building one from blank records, with no ExpressNet
anywhere, is untested. The zone-table packer was written from a
decompile of ExpressNet's own packing code and checked against nine real
downloads. Treat a field you have not seen work on a board as a guess.

## How it was worked out

The XNet protocol was read off the line from our own Protel board's
call-ins. No manual publishes it. Some details were confirmed by running
the phone's firmware in an emulator, and the record layouts were mapped
by changing one field at a time in ExpressNet and comparing the files; that emulator and the firmware are
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
