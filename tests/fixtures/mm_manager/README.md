# mm_manager's shipped tables, as test fixtures

Copied unchanged from `tables/default/` in
[hharte/mm_manager](https://github.com/hharte/mm_manager) at commit `2a23e8c`.
MIT, like this repo; its licence is `LICENSE` beside them.

They are what a Millennium receives for any table it has no file of its own
for, so they are the tables every edit starts from. The tests
round-trip each one byte for byte: a renderer that changed a byte nobody
edited would hand the phone a different table than the one it is running,
and that difference would show up only as a call the phone rated wrongly.

| file | table |
|---|---|
| `mm_table_16.bin` | CARD, MTR 1.x (20 × 33 bytes) |
| `mm_table_18.bin` | call screening, universal, MTR 1.7 (60 × 12) |
| `mm_table_49.bin` | RATE (header + 128 × 9) |
| `mm_table_5c.bin` | call screening, MTR 1.9+ (180 × 17) |
| `mm_table_86.bin` | CARD, expanded, MTR 2.x (32 × 36) |
| `mm_table_4a`-`51`, `5a`, `5b` | LCD, MTR 1.7 (uncompressed, 818 bytes): upstream's San Jose area, 408 first |
| `mm_table_65`-`6b` | LCD, MTR 1.9 (compressed, 402 bytes) |
| `mm_table_88`-`91` | LCD, MTR 1.20/2.x (double-compressed, 202 bytes) |

The LCD tables are here for two reasons: the encoder in `common/millennium.py`
is checked against every one of them byte for byte, and a beta phone's folder
must override every LCD slot the default set fills -- a slot it leaves empty
is sent from here, and would give a stranger's phone San Jose's local calls.
