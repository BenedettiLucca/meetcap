"""Synthetic data fixtures for Meetcap testing.

Never contains real audio, transcripts, or personal data.
Generates minimal valid synthetic files for testing.
"""

from pathlib import Path
import wave
from typing import Union


def generate_synthetic_wav_bytes(duration_ms: int = 100, sample_rate: int = 16000) -> bytes:
    """Generate in-memory bytes of a valid minimal PCM WAV file."""
    num_frames = int(sample_rate * (duration_ms / 1000.0))
    raw_pcm = b"\x00\x00" * num_frames

    # RIFF header: 44 bytes total
    header = bytearray()
    header.extend(b"RIFF")
    file_size_minus_8 = 36 + len(raw_pcm)
    header.extend(file_size_minus_8.to_bytes(4, "little"))
    header.extend(b"WAVEfmt ")
    header.extend((16).to_bytes(4, "little"))  # subchunk1size (16 for PCM)
    header.extend((1).to_bytes(2, "little"))   # audio format (1 = PCM)
    header.extend((1).to_bytes(2, "little"))   # num channels (1 = mono)
    header.extend(sample_rate.to_bytes(4, "little"))
    byte_rate = sample_rate * 1 * 2  # sample_rate * channels * bits_per_sample / 8
    header.extend(byte_rate.to_bytes(4, "little"))
    block_align = 1 * 2  # channels * bits_per_sample / 8
    header.extend(block_align.to_bytes(2, "little"))
    header.extend((16).to_bytes(2, "little"))  # bits per sample (16)
    header.extend(b"data")
    header.extend(len(raw_pcm).to_bytes(4, "little"))

    return bytes(header) + raw_pcm


def create_synthetic_wav(path: Union[Path, str], duration_ms: int = 100, sample_rate: int = 16000) -> Path:
    """Create a minimal valid synthetic WAV file on disk."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(generate_synthetic_wav_bytes(duration_ms, sample_rate))
    return dest


def create_synthetic_transcript(path: Union[Path, str], title: str = "Synthetic Meeting") -> Path:
    """Create a minimal synthetic transcript text file on disk."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# Meetcap Transcript\n"
        f"Date: 2026-01-01 10:00\n"
        f"File: {dest.with_suffix('.wav').name}\n"
        f"Model: test-model (cpu/int8)\n"
        f"Language: en (99.0%)\n"
        f"Duration: 10.0s\n"
        f"\n---\n\n"
        f"[00:00 → 00:05] Hello, this is a synthetic test meeting.\n"
        f"[00:05 → 00:10] Action item: verify daemon integration.\n"
    )
    dest.write_text(content, encoding="utf-8")
    return dest
