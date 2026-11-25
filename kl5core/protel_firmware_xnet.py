"""DD8822 memory-frame details recovered by executing the ROM handlers.

DD8822 validates a second, header-only checksum before an E write and includes
one before D reply data. The shared D decoder also verifies record envelopes.
The complete configuration checksum is independent of both frame checksums.
"""

from . import xnetproto as xp


CONFIGURATION_START = 0x1000
CONFIGURATION_END = 0x2D00  # exclusive; FB8E stops when the page reaches $2D
CONFIGURATION_LENGTH = CONFIGURATION_END - CONFIGURATION_START


def configuration_checksum(image):
    """Independent FB8E result: $AAAA plus bytes $1002..$2CFF, modulo 65536.

    This is an image inspection tool. It never repairs the stored checksum.
    """
    image = bytes(image)
    if len(image) != CONFIGURATION_LENGTH:
        raise ValueError('DD8822 configuration must contain exactly 7424 bytes')
    return (0xAAAA + sum(image[2:])) & 0xFFFF


def configuration_writes(image):
    """Preflight a complete checksum-valid image, then frame all E writes.

    Whole-image validity is separate from both XNet checksums. Neither a
    valid sum nor an ACK proves field compatibility or physical bank wiring.
    """
    image = bytes(image)
    if configuration_checksum(image) != int.from_bytes(image[:2], 'little'):
        raise ValueError('DD8822 stored configuration checksum mismatch')
    return [memory_write(CONFIGURATION_START + offset, image[offset:offset + 250])
            for offset in range(0, len(image), 250)]


def _integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("%s must be %d..%d" % (name, minimum, maximum))
    return value


def memory_read(address, length):
    address = _integer(address, 0, 0xFFFF, "address")
    length = _integer(length, 1, 250, "read length")
    if address + length > 0x10000:
        raise ValueError("read crosses the CPU address space")
    return xp.frame("D", address.to_bytes(2, "big") + bytes([length]))


def memory_write(address, data):
    address = _integer(address, 0x1000, 0x7FFF, "write address")
    data = bytes(data)
    if not 1 <= len(data) <= 250 or address + len(data) > 0x8000:
        raise ValueError("write must contain 1..250 bytes within $1000-$7FFF")
    addr = address.to_bytes(2, "big")
    header = bytes([xp.SYNC, len(data) + 5, ord("E")]) + addr
    return xp.frame("E", addr + bytes([xp.checksum(header)]) + data)


def bank_fill(bank, address, length, value):
    bank = _integer(bank, 0, 3, "SRAM bank")
    address = _integer(address, 0x0200, 0x7FFF, "fill address")
    length = _integer(length, 1, 0x7E00, "fill length")
    value = _integer(value, 0, 255, "fill value")
    if address + length > 0x8000:
        raise ValueError("fill crosses the experimental SRAM window")
    payload = bytes([ord("A"), 0, bank]) + address.to_bytes(2, "big")
    return xp.frame("B", payload + length.to_bytes(2, "big") + bytes([value]))


def read_answer(packet):
    """Decode a verified outer D packet and validate its inner header checksum."""
    return xp.read_answer(packet)



def configuration_from_packets(packets):
    """Resolve explicit bank-zero fills/writes; never assume missing bytes zero."""
    memory = {}
    for packet in packets:
        if packet.type == 'B':
            p = packet.payload
            if len(p) != 8 or p[:3] != b'A\0\0':
                raise ValueError('preflight supports only explicit bank-zero fills')
            address = int.from_bytes(p[3:5], 'big')
            length = int.from_bytes(p[5:7], 'big')
            if not length or address < CONFIGURATION_START or address + length > CONFIGURATION_END:
                raise ValueError('fill is outside the configuration window')
            memory.update((a, p[7]) for a in range(address, address + length))
        elif packet.type == 'E':
            address, data = xp.write_request(packet)
            if address < CONFIGURATION_START or address + len(data) > CONFIGURATION_END:
                raise ValueError('write is outside the configuration window')
            memory.update((address + i, value) for i, value in enumerate(data))
    if any(a not in memory for a in range(CONFIGURATION_START, CONFIGURATION_END)):
        raise ValueError('download does not explicitly cover all 7424 configuration bytes')
    image = bytes(memory[a] for a in range(CONFIGURATION_START, CONFIGURATION_END))
    configuration_writes(image)
    return image
