#!/usr/bin/env python3
"""Convert pymobiledevice3 pcapng captures to classic Ethernet pcap."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from pcapng import FileScanner, blocks


PCAP_HEADER = struct.pack(
    "<IHHIIII",
    0xA1B2C3D4,
    2,
    4,
    0,
    0,
    262_144,
    1,
)


def convert(source: Path, target: Path) -> tuple[int, int]:
    written = 0
    skipped = 0
    with source.open("rb") as src, target.open("wb") as dst:
        dst.write(PCAP_HEADER)
        for block in FileScanner(src):
            if not isinstance(block, blocks.EnhancedPacket):
                continue
            interface = block.interface_id
            description = block.section.interfaces[interface]
            if description.link_type != 1:
                skipped += 1
                continue

            packet = block.packet_data
            timestamp = block.timestamp
            seconds = int(timestamp)
            microseconds = int(round((timestamp - seconds) * 1_000_000))
            if microseconds >= 1_000_000:
                seconds += 1
                microseconds -= 1_000_000
            dst.write(struct.pack("<IIII", seconds, microseconds, len(packet), len(packet)))
            dst.write(packet)
            written += 1
    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert pymobiledevice3 pcapng output to classic Ethernet pcap."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()

    written, skipped = convert(args.source, args.target)
    print(f"wrote {written} Ethernet packets to {args.target}; skipped {skipped}")


if __name__ == "__main__":
    main()
