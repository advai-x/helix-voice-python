import os
import sys

from helixvoice import HelixVoiceClient


if len(sys.argv) != 2:
    raise SystemExit("Usage: python transcribe.py <audio-file>")

base_url = os.getenv("HELIXVOICE_BASE_URL") or "https://api.helixvoice.net"
with HelixVoiceClient(base_url=base_url) as client:
    result = client.transcriptions.create(file=sys.argv[1], language="zh-CN")
    print(result.text)
