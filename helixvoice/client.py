"""Server-side developer API and independent anonymous preview client."""

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Optional, Union
from urllib.parse import quote, urlsplit

import urllib3

from .models.capabilities import Capabilities
from .models.generation import Generation
from .models.transcription import Transcription
from .models.voice import Voice

BASE_URL = "https://api.helixvoice.net"
_MAX_WAV = 16 * 1024 * 1024
_MAX_UPLOAD = 20 * 1024 * 1024
_MAX_JSON = 4 * 1024 * 1024
FilePath = Union[str, os.PathLike]


class HelixVoiceError(Exception):
    """An HTTP or response-format error. Retry-After is the original header value."""

    def __init__(self, message, *, status, code=None, request_id=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after


class SpeechSaveError(Exception):
    """Generation succeeded. Recover its WAV later using generation.id."""

    def __init__(self, generation: Generation):
        super().__init__("Speech was generated but its WAV could not be saved")
        self.generation = generation


@dataclass(frozen=True)
class PreviewResult:
    audio: bytes
    cache: Optional[str]


class _Transport:
    def __init__(self, base_url: str, timeout: float, api_key: Optional[str] = None):
        url = urlsplit(base_url)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.query or url.fragment):
            raise ValueError("base_url must be an HTTP(S) base URL")
        self.base_url = base_url.rstrip("/")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = urllib3.Timeout(total=timeout)
        self.pool = urllib3.PoolManager()
        self.api_key = api_key

    def request(self, method, route, *, payload=None, fields=None, result="json"):
        headers = {"Accept": "audio/wav" if result == "wav" else "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if fields is not None:
            body, headers["Content-Type"] = urllib3.encode_multipart_formdata(fields)

        response = self.pool.request(
            method, self.base_url + route, body=body, headers=headers,
            redirect=False, retries=False, timeout=self.timeout, preload_content=False,
        )
        status = response.status

        def failure(message, code=None, request_id=None):
            return HelixVoiceError(
                message, status=status, code=code,
                request_id=request_id or response.headers.get("X-Request-ID"),
                retry_after=response.headers.get("Retry-After"),
            )

        try:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if not 200 <= status < 300:
                details = {}
                if content_type == "application/json":
                    try:
                        error = json.loads(response.read(64 * 1024 + 1))
                        if isinstance(error, dict):
                            details = error.get("error", error)
                    except (ValueError, UnicodeError):
                        pass
                if not isinstance(details, dict):
                    details = {}
                code = details.get("code")
                raise failure(
                    details.get("message") or f"Helix Voice returned HTTP {status}",
                    str(code) if code is not None else None,
                    details.get("requestId") or details.get("transactionId"),
                )
            if result == "empty" and status == 204:
                response.read()
                return None
            if result == "wav" and content_type == "audio/wav":
                data = response.read(_MAX_WAV + 1)
                if len(data) > _MAX_WAV:
                    raise failure("WAV exceeds the SDK response size limit")
                return PreviewResult(data, response.headers.get("X-TTS-Preview-Cache"))
            if result == "json" and content_type == "application/json":
                try:
                    data = response.read(_MAX_JSON + 1)
                    if len(data) <= _MAX_JSON:
                        envelope = json.loads(data)
                        if isinstance(envelope, dict) and envelope.get("data") is not None:
                            return envelope["data"]
                except ValueError:
                    pass
            raise failure("Unexpected Helix Voice response")
        finally:
            response.close()
            response.release_conn()

    def close(self):
        self.pool.clear()


def _segment(value: str) -> str:
    if not value or value in (".", ".."):
        raise ValueError("A resource ID is required")
    return quote(value, safe="")


def _upload(path: FilePath):
    source = Path(path)
    with source.open("rb") as stream:
        content = stream.read(_MAX_UPLOAD + 1)
    if len(content) > _MAX_UPLOAD:
        raise ValueError("Upload exceeds 20 MiB")
    mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return source.name, content, mime_type


def _save_wav(path: FilePath, data: bytes):
    destination = Path(path)
    with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


class _Capabilities:
    def __init__(self, transport):
        self._http = transport

    def get(self) -> Capabilities:
        return Capabilities.model_validate(self._http.request("GET", "/v1/capabilities"))


class _Voices:
    def __init__(self, transport):
        self._http = transport

    def list(self) -> list[Voice]:
        return [Voice.model_validate(voice) for voice in self._http.request("GET", "/v1/voices")]

    def clone(self, *, file: FilePath, name: str, language: Literal["zh-CN", "en-US"],
              transcript: str, consent: bool) -> Voice:
        if consent is not True:
            raise ValueError("Explicit consent is required to clone a voice")
        fields = {"file": _upload(file), "name": name, "language": language,
                  "transcript": transcript, "consent": "true"}
        return Voice.model_validate(self._http.request("POST", "/v1/voices", fields=fields))

    def delete(self, id: str) -> None:
        self._http.request("DELETE", "/v1/voices/" + _segment(id), result="empty")


class _Generations:
    def __init__(self, transport):
        self._http = transport

    def list(self) -> list[Generation]:
        return [Generation.model_validate(item) for item in self._http.request("GET", "/v1/generations")]

    def download(self, id: str) -> bytes:
        return self._http.request("GET", "/v1/generations/" + _segment(id) + "/audio", result="wav").audio

    def delete(self, id: str) -> None:
        self._http.request("DELETE", "/v1/generations/" + _segment(id), result="empty")


class _Speech:
    def __init__(self, transport, generations):
        self._http = transport
        self._generations = generations

    def generate(self, *, input: str, voice: str) -> Generation:
        return Generation.model_validate(self._http.request(
            "POST", "/v1/audio/speech", payload={"input": input, "voice": voice},
        ))

    def save(self, *, input: str, voice: str, output: FilePath) -> Generation:
        generation = self.generate(input=input, voice=voice)
        try:
            _save_wav(output, self._generations.download(generation.id))
        except Exception as error:
            raise SpeechSaveError(generation) from error
        return generation


class _Transcriptions:
    def __init__(self, transport):
        self._http = transport

    def create(self, *, file: FilePath, language: Optional[Literal["zh-CN", "auto"]] = None) -> Transcription:
        fields = {"file": _upload(file)}
        if language is not None:
            fields["language"] = language
        return Transcription.model_validate(self._http.request("POST", "/v1/audio/transcriptions", fields=fields))


class HelixVoiceClient:
    def __init__(self, api_key: Optional[str] = None, *, base_url: str = BASE_URL, timeout: float = 180):
        key = (os.getenv("HELIXVOICE_API_KEY", "") if api_key is None else api_key).strip()
        if not key:
            raise ValueError("A Helix Voice API key is required")
        self._http = _Transport(base_url, timeout, key)
        self.capabilities = _Capabilities(self._http)
        self.voices = _Voices(self._http)
        self.generations = _Generations(self._http)
        self.speech = _Speech(self._http, self.generations)
        self.transcriptions = _Transcriptions(self._http)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class HelixVoicePreviewClient:
    def __init__(self, *, base_url: str = BASE_URL, timeout: float = 180):
        self._http = _Transport(base_url, timeout)

    def generate(self, *, input: str, voice: Optional[str] = None) -> PreviewResult:
        payload = {"input": input}
        if voice is not None:
            payload["voice"] = voice
        return self._http.request("POST", "/v1/audio/preview", payload=payload, result="wav")

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
