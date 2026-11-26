import os

from helixvoice import HelixVoiceClient


base_url = os.getenv("HELIXVOICE_BASE_URL") or "https://api.helixvoice.net"
with HelixVoiceClient(base_url=base_url) as client:
    voices = client.voices.list()
    if not voices:
        raise SystemExit("No voices are available to this account.")

    voice = voices[0]
    generation = client.speech.save(
        input="Hello from Helix Voice.", voice=voice.id, output="speech.wav"
    )

print(f"Saved speech.wav with voice {voice.id} (generation {generation.id})")
