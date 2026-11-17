"""Real local Faster-Whisper transcription without Jarvis or browser."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import load_settings
from jarvis.voice import VoiceTranscriber


async def main() -> None:
    transcriber = VoiceTranscriber(load_settings().data_dir)
    sample = Path(sys.argv[1]).read_bytes()
    try:
        result = await transcriber.transcribe(sample)
        assert result["text"] and result["audio_seconds"] > 0
        print(result)
    finally:
        await transcriber.close()


if __name__ == "__main__":
    asyncio.run(main())
