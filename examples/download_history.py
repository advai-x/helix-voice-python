import os
from pathlib import Path
import sys

from helixvoice import HelixVoiceClient


if len(sys.argv) > 2:
    raise SystemExit("Usage: python download_history.py [generation-id]")

base_url = os.getenv("HELIXVOICE_BASE_URL") or "https://api.helixvoice.net"
with HelixVoiceClient(base_url=base_url) as client:
    if len(sys.argv) == 1:
        for generation in client.generations.list():
            print(generation.id)
    else:
        audio = client.generations.download(sys.argv[1])
        Path("history.wav").write_bytes(audio)
        print("Saved history.wav")
