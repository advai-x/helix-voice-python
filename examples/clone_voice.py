import os
import sys

from helixvoice import HelixVoiceClient


if len(sys.argv) != 6 or sys.argv[1] != "--consent":
    raise SystemExit("Usage: python clone_voice.py --consent <reference-audio> <name> <locale> <transcript>")

_, _, audio_file, name, locale, transcript = sys.argv
base_url = os.getenv("HELIXVOICE_BASE_URL") or "https://api.helixvoice.net"
with HelixVoiceClient(base_url=base_url) as client:
    voice = client.voices.clone(
        file=audio_file, name=name, language=locale, transcript=transcript, consent=True
    )
    print(voice.id)
