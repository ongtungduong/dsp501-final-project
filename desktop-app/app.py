"""Audio fingerprinting desktop client.

Records a short clip from the default microphone, sends it to the project
HTTP API, and renders the match result plus the server-rendered spectrogram
in a small tkinter window.

The two endpoints it talks to:

  POST /api/match        — multipart upload of a WAV file, returns match info
  POST /api/spectrogram  — multipart upload of a WAV file, returns a PNG

Both endpoints are documented in ``src/server/routes.py``. The server
handles decoding, fingerprinting, and rendering; this client is just a
microphone + a transport.
"""

from __future__ import annotations

import io
import json
import os
import threading
import tkinter as tk
import wave
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

import numpy as np
import requests
import sounddevice as sd
from PIL import Image, ImageTk

# Server endpoints. The base URL is configurable from the UI so the same
# binary can be pointed at any host (local dev, docker, a deployed instance)
# without rebuilding.
DEFAULT_API_BASE = "http://127.0.0.1:8000"
MATCH_PATH = "/api/match"
SPECTROGRAM_PATH = "/api/spectrogram"

# Capture parameters. Five seconds is long enough to give the matcher hashes to
# score against, short enough that the user does not get bored waiting. Note
# this is shorter than the web client and `shazam listen`, which both record 8
# seconds: a shorter clip yields fewer hashes and therefore a lower score, and
# microphone audio already scores lowest of any query type.
SAMPLE_RATE = 22_050
CHANNELS = 1
DURATION_SECONDS = 5.0
DTYPE = "float32"

# HTTP status treated as a successful response. The server's two endpoints
# both return 200; anything else is an error worth surfacing as-is.
HTTP_OK = 200


@dataclass(frozen=True)
class MatchResult:
    """Typed view of the JSON returned by ``/api/match``."""

    matched: bool
    title: str | None
    artist: str | None
    score: int
    strength: str | None
    aligned_fraction: float
    offset_seconds: float
    query_hashes: int
    elapsed_ms: int

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> MatchResult:
        match = payload.get("match")
        if match is None:
            return cls(
                matched=False,
                title=None,
                artist=None,
                score=0,
                strength=None,
                aligned_fraction=0.0,
                offset_seconds=0.0,
                query_hashes=int(payload.get("queryHashes", 0)),
                elapsed_ms=int(payload.get("elapsedMs", 0)),
            )
        return cls(
            matched=True,
            title=str(match.get("title", "")),
            artist=match.get("artist"),
            score=int(match.get("score", 0)),
            strength=str(match.get("strength", "")),
            aligned_fraction=float(match.get("alignedFraction", 0.0)),
            offset_seconds=float(match.get("offsetSeconds", 0.0)),
            query_hashes=int(payload.get("queryHashes", 0)),
            elapsed_ms=int(payload.get("elapsedMs", 0)),
        )


class AudioRecorder:
    """Records a single mono clip from the default input device.

    Kept deliberately small — the GUI owns the lifecycle, this just pushes
    samples into a numpy array under a callback.
    """

    def __init__(self, sample_rate: int, channels: int, dtype: str) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        # `None` means "let PortAudio pick the default", which mirrors the
        # behaviour before the device selector was added.
        self._device: int | str | None = None

    def configure_device(self, device: int | str | None) -> None:
        """Pin the input device used by the next ``start()``.

        Pass ``None`` to fall back to the OS default. The change takes
        effect on the next call to ``start()`` so the existing recording
        is never disturbed mid-flight.
        """
        self._device = device

    def _callback(self, indata: np.ndarray, _frames: int, _time: object, _status: object) -> None:
        # Copy: sounddevice reuses the buffer between callbacks. Holding the
        # lock briefly so a concurrent stop() does not race with append.
        with self._lock:
            self._frames.append(indata.copy())

    def start(self) -> None:
        with self._lock:
            self._frames = []
        kwargs: dict[str, Any] = {
            "samplerate": self.sample_rate,
            "channels": self.channels,
            "dtype": self.dtype,
            "callback": self._callback,
        }
        if self._device is not None:
            kwargs["device"] = self._device
        self._stream = sd.InputStream(**kwargs)
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._frames:
                return np.zeros((0, self.channels), dtype=np.dtype(self.dtype))
            return np.concatenate(self._frames, axis=0)


def signal_to_wav_bytes(signal: np.ndarray, sample_rate: int) -> bytes:
    """Render a normalised mono float32 signal as a 16-bit PCM WAV.

    The server uses libsndfile which accepts anything, but WAV is the
    lowest-common-denominator format and matches what the web client sends.
    Peak-normalising before quantising keeps the matcher from seeing a
    quieter-than-original recording just because the user spoke softly.

    Returns:
        A complete 16-bit PCM mono WAV file as bytes, ready to upload.
    """
    if signal.ndim > 1:
        signal = signal[:, 0]
    signal = signal.astype(np.float32, copy=False)
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    if peak > 0.0:
        signal /= peak
    pcm = np.clip(signal, -1.0, 1.0)
    pcm = (pcm * 32_767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


# Sentinel placed at the top of the device combobox to mean "let PortAudio
# pick the OS default". The current default is resolved by querying
# `sounddevice` at the moment the user clicks the record button, so the
# choice stays accurate even if the user swaps their default in the OS
# settings panel while the app is open.
DEFAULT_DEVICE_LABEL = "Mặc định của hệ thống"


def _query_input_devices() -> list[tuple[str, int | None]]:
    """Return ``[(label, device_index_or_none), ...]`` for every input device.

    The first entry is always the :data:`DEFAULT_DEVICE_LABEL` sentinel
    mapped to ``None`` — selecting it in the combobox lets PortAudio pick
    the system default at ``start()`` time.

    Returns:
        A list of ``(label, device_index)`` pairs. The first entry is the
        "default" sentinel; the rest are sorted by device index. Output
        devices and zero-channel entries are filtered out.
    """
    entries: list[tuple[str, int | None]] = [(DEFAULT_DEVICE_LABEL, None)]
    try:
        devices = sd.query_devices()
    except Exception as exc:  # broad catch: device probe should never crash the UI
        return [(DEFAULT_DEVICE_LABEL, None), (f"(Lỗi truy vấn thiết bị: {exc})", None)]
    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) <= 0:
            continue
        label = str(info.get("name", f"Thiết bị {index}"))
        entries.append((f"{index}: {label}", index))
    return entries


class ShazamDesktopApp:
    """Main tkinter window. Single-page UI: one button, one result card, one image."""

    def __init__(self, root: tk.Tk, config: _Config) -> None:
        self.root = root
        self.root.title("Shazam — Nhận diện âm thanh")
        self.root.geometry("960x880")
        self.root.minsize(720, 720)

        self.recorder = AudioRecorder(SAMPLE_RATE, CHANNELS, DTYPE)
        self._is_recording = False
        self._worker: threading.Thread | None = None
        # Hold a reference so the image is not garbage-collected the moment
        # tkinter drops its pointer to it.
        self._current_image: ImageTk.PhotoImage | None = None
        self._initial_api_base = config.api_base

        self._build_ui()

    def _build_ui(self) -> None:
        # Top bar: two rows. Row 1 is the API URL (rarely changed); row 2
        # holds the mic picker, the record button, and the status text.
        # Putting the combobox in its own row keeps it wide enough to read
        # the device name on every host.
        top = ttk.Frame(self.root, padding=12)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="API URL:").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.api_url_var = tk.StringVar(value=self._initial_api_base)
        ttk.Entry(top, textvariable=self.api_url_var, width=40).grid(
            row=0, column=1, sticky="we", padx=(6, 0), pady=(0, 6)
        )
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Mic:").grid(row=1, column=0, sticky="w")
        self._device_options: list[tuple[str, int | None]] = []
        self.device_var = tk.StringVar(value=DEFAULT_DEVICE_LABEL)
        self.device_combo = ttk.Combobox(
            top,
            textvariable=self.device_var,
            state="readonly",
            width=40,
        )
        self.device_combo.grid(row=1, column=1, sticky="we", padx=(6, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_changed)

        self.refresh_button = ttk.Button(
            top, text="↻", width=3, command=self._refresh_devices
        )
        self.refresh_button.grid(row=1, column=2, padx=(6, 0))

        self.record_button = ttk.Button(
            top, text="Bắt đầu nhận diện", command=self._on_record_clicked
        )
        self.record_button.grid(row=1, column=3, padx=(12, 0))

        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(top, textvariable=self.status_var, foreground="#555").grid(
            row=1, column=4, sticky="w", padx=(16, 0)
        )

        self._refresh_devices()

        # Result panel. The match outcome lives above the spectrogram so a
        # user who only cares about the title does not have to scroll.
        result_frame = ttk.LabelFrame(self.root, text="Kết quả", padding=12)
        result_frame.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(0, 8))

        self.result_title_var = tk.StringVar(value="—")
        self.result_artist_var = tk.StringVar(value="—")
        self.result_metrics_var = tk.StringVar(value="—")

        ttk.Label(result_frame, text="Bài hát:", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=2
        )
        ttk.Label(result_frame, textvariable=self.result_title_var).grid(
            row=0, column=1, sticky="w", pady=2
        )
        ttk.Label(result_frame, text="Nghệ sĩ:", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=2
        )
        ttk.Label(result_frame, textvariable=self.result_artist_var).grid(
            row=1, column=1, sticky="w", pady=2
        )
        ttk.Label(result_frame, text="Độ đo:", font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, sticky="nw", padx=(0, 6), pady=2
        )
        ttk.Label(result_frame, textvariable=self.result_metrics_var, justify="left").grid(
            row=2, column=1, sticky="w", pady=2
        )
        result_frame.columnconfigure(1, weight=1)

        # Spectrogram panel. Canvas with a fixed aspect-ish area so the
        # PNG always lands inside the window instead of overflowing.
        image_frame = ttk.LabelFrame(self.root, text="Spectrogram", padding=12)
        image_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.image_label = ttk.Label(image_frame, text="(Chưa có dữ liệu)", anchor="center")
        self.image_label.pack(fill=tk.BOTH, expand=True)

        # Raw API response panel. Read-only scrolled text so the user can
        # inspect the exact JSON the server returned — useful for debugging
        # the camelCase shape and for seeing fields the UI does not surface
        # (everything under `match` is shown, just formatted nicely).
        response_frame = ttk.LabelFrame(self.root, text="Response JSON", padding=4)
        response_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=12, pady=(0, 12))
        self.response_text = scrolledtext.ScrolledText(
            response_frame,
            height=10,
            wrap=tk.NONE,
            font=("Consolas", 9),
            background="#fafafa",
        )
        self.response_text.pack(fill=tk.BOTH, expand=True)
        self.response_text.insert("1.0", "(Chưa có response)")
        self.response_text.config(state=tk.DISABLED)

    def _set_status(self, text: str) -> None:
        # Marshalled onto the main thread so tkinter widgets never get
        # touched from a worker thread.
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_button_state(self, *, recording: bool = False, busy: bool = False) -> None:
        """Update the record button into one of three explicit states.

        Using keyword-only args avoids the trap of ``_set_button_state(recording=True)``
        vs ``_set_button_state(busy=True)`` — every call site now reads as English.

        The mic picker and refresh button share the disabled state with
        the record button: changing them mid-recording would change the
        device the next ``start()`` opens, which is rarely what the user
        wants and tends to surface as a confusing "different mic" later.
        """

        def update() -> None:
            if recording:
                self.record_button.config(
                    text="Đang nghe…",
                    state=tk.DISABLED,
                )
            elif busy:
                self.record_button.config(
                    text="Đang xử lý…",
                    state=tk.DISABLED,
                )
            else:
                self.record_button.config(
                    text="Bắt đầu nhận diện",
                    state=tk.NORMAL,
                )
            combo_state = tk.DISABLED if (recording or busy) else "readonly"
            self.device_combo.config(state=combo_state)
            self.refresh_button.config(
                state=tk.DISABLED if (recording or busy) else tk.NORMAL,
            )

        self.root.after(0, update)

    def _refresh_devices(self) -> None:
        """Reload the mic list from `sounddevice` and reselect the current choice.

        Combo boxes index by string, so we keep an internal list of
        ``(label, device_index)`` pairs and rebuild the box's value list
        on every refresh. The selected device is preserved when its name
        still shows up; otherwise the user is bumped back to the OS
        default so the next recording still works.
        """
        previous = self.device_var.get()
        self._device_options = _query_input_devices()
        labels = [label for label, _ in self._device_options]
        self.device_combo["values"] = labels
        if previous in labels:
            self.device_var.set(previous)
        else:
            self.device_var.set(DEFAULT_DEVICE_LABEL)
        self._apply_device_selection()

    def _on_device_changed(self, _event: object) -> None:
        self._apply_device_selection()

    def _apply_device_selection(self) -> None:
        """Push the combobox's current value into the recorder."""
        selection = self.device_var.get()
        match = next(
            (index for label, index in self._device_options if label == selection),
            None,
        )
        self.recorder.configure_device(match)

    def _on_record_clicked(self) -> None:
        if self._is_recording or self._worker is not None:
            return
        self._is_recording = True
        self._set_button_state(recording=True)
        self._set_status("Đang mở micro…")
        try:
            self.recorder.start()
        except Exception as exc:
            self._is_recording = False
            self._set_button_state()
            self._set_status(f"Lỗi micro: {exc}")
            messagebox.showerror("Lỗi micro", str(exc))
            return

        # Count down so the user knows when the recording ends. One timer
        # is enough — the worker thread closes the recorder when it is
        # done, and that is the source of truth.
        self.root.after(int(DURATION_SECONDS * 1000), self._finalize_recording)

    def _finalize_recording(self) -> None:
        if not self._is_recording:
            return
        self._is_recording = False
        signal = self.recorder.stop()
        if signal.size == 0:
            self._set_button_state()
            self._set_status("Không thu được âm thanh, thử lại.")
            messagebox.showwarning("Không có dữ liệu", "Micro không trả về dữ liệu.")
            return

        self._set_button_state(busy=True)
        self._set_status("Đang gửi lên server…")
        self._set_response_text("Đang chờ response từ server…")
        self._worker = threading.Thread(
            target=self._send_to_server, args=(signal,), daemon=True
        )
        self._worker.start()

    def _send_to_server(self, signal: np.ndarray) -> None:
        try:
            api_base = self._current_api_base()
            wav_bytes = signal_to_wav_bytes(signal, SAMPLE_RATE)
            outcome = self._run_match_and_spectrogram(api_base, wav_bytes)
        except requests.RequestException as exc:
            self._show_error(f"Không kết nối được server: {exc}", "")
            self._set_response_text(
                f"Lỗi kết nối:\n  {type(exc).__name__}: {exc}"
            )
        except Exception as exc:  # broad catch: UI boundary, surface everything
            self._show_error(f"Lỗi không mong đợi: {exc}", "")
            self._set_response_text(
                f"Lỗi không mong đợi:\n  {type(exc).__name__}: {exc}"
            )
        else:
            if outcome is not None:
                self._show_success(*outcome)
        finally:
            self._worker = None

    def _run_match_and_spectrogram(
        self, api_base: str, wav_bytes: bytes
    ) -> tuple[MatchResult, dict[str, Any], requests.Response, bytes] | None:
        """Drive the two HTTP calls.

        Returns:
            A ``(match_result, match_payload, spec_response, png_bytes)``
            tuple on the happy path, or ``None`` on any failure (the helper
            has already surfaced the error to the UI in that case).
        """
        match_payload, match_response = self._call_match(api_base, wav_bytes)
        if match_payload is None or match_response is None:
            return None
        match_result = MatchResult.from_json(match_payload)
        spec_response = self._call_spectrogram(
            api_base, wav_bytes, match_response, match_payload
        )
        if spec_response is None:
            return None
        return match_result, match_payload, spec_response, spec_response.content

    def _current_api_base(self) -> str:
        return self.api_url_var.get().strip().rstrip("/") or DEFAULT_API_BASE

    @staticmethod
    def _audio_files(wav_bytes: bytes) -> dict[str, tuple[str, bytes, str]]:
        return {"file": ("query.wav", wav_bytes, "audio/wav")}

    def _call_match(
        self, api_base: str, wav_bytes: bytes
    ) -> tuple[dict[str, Any] | None, requests.Response | None]:
        """POST to ``/api/match`` and surface any non-200 response.

        Returns:
            ``(payload, response)`` on success. ``(None, response)`` on a
            non-200 response — the error has already been shown to the
            user, so the caller treats it as a stop.
        """
        # Match first so the user gets the verdict quickly even if the
        # spectrogram render takes a moment.
        response = requests.post(
            f"{api_base}{MATCH_PATH}",
            files=self._audio_files(wav_bytes),
            timeout=30,
        )
        if response.status_code != HTTP_OK:
            self._show_error(
                f"Server từ chối /match (HTTP {response.status_code}).",
                response.text,
            )
            # Show the raw body anyway — it is usually the helpful bit
            # (a 422 message, a FastAPI validation trace, etc.).
            self._set_response_text(
                self._format_raw_response(response, hint="Body from /api/match")
            )
            return None, response
        return response.json(), response

    def _call_spectrogram(
        self,
        api_base: str,
        wav_bytes: bytes,
        match_response: requests.Response,
        match_payload: dict[str, Any],
    ) -> requests.Response | None:
        """POST to ``/api/spectrogram`` and surface any non-200 response.

        Returns:
            The response on success, or ``None`` on a non-200 response
            (the error has already been shown to the user).
        """
        # Re-open the same WAV for the spectrogram call. `requests` does
        # not let us rewind the previous upload, so we read the bytes
        # out of the buffer we built above.
        response = requests.post(
            f"{api_base}{SPECTROGRAM_PATH}",
            files=self._audio_files(wav_bytes),
            timeout=30,
        )
        if response.status_code != HTTP_OK:
            self._show_error(
                f"Server từ chối /spectrogram (HTTP {response.status_code}).",
                response.text,
            )
            self._set_response_text(
                self._format_both_responses(
                    match_response,
                    response,
                    match_payload=match_payload,
                )
            )
            return None
        return response

    def _format_raw_response(self, response: requests.Response, hint: str = "") -> str:
        lines = [self._format_response_header(response, hint)]
        body = response.text
        if body:
            lines.extend(("", self._try_pretty_json(body)))
        return "\n".join(lines)

    def _format_both_responses(
        self,
        match_response: requests.Response,
        spec_response: requests.Response,
        match_payload: dict[str, Any] | None = None,
    ) -> str:
        match_part = self._format_response_header(match_response, "POST /api/match")
        if match_payload is not None:
            match_part += "\n\n" + json.dumps(match_payload, indent=2, ensure_ascii=False)
        else:
            text = match_response.text
            match_part += "\n\n" + (self._try_pretty_json(text) if text else "(empty body)")
        spec_part = self._format_response_header(spec_response, "POST /api/spectrogram")
        spec_body = spec_response.text
        if spec_body:
            spec_part += "\n\n" + self._try_pretty_json(spec_body)
        return f"{match_part}\n\n{'-' * 60}\n\n{spec_part}"

    @staticmethod
    def _format_response_header(response: requests.Response, label: str) -> str:
        content_type = response.headers.get("content-type", "(none)")
        content_length = response.headers.get("content-length", "(none)")
        return (
            f"{label}\n"
            f"  status: {response.status_code}\n"
            f"  content-type: {content_type}\n"
            f"  content-length: {content_length} bytes"
        )

    @staticmethod
    def _try_pretty_json(text: str) -> str:
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except (ValueError, TypeError):
            return text

    def _set_response_text(self, text: str) -> None:
        # tkinter text widgets are not thread-safe, so hop to the main
        # thread before touching them.
        def update() -> None:
            self.response_text.config(state=tk.NORMAL)
            self.response_text.delete("1.0", tk.END)
            self.response_text.insert("1.0", text)
            self.response_text.config(state=tk.DISABLED)

        self.root.after(0, update)

    def _show_success(
        self,
        result: MatchResult,
        match_payload: dict[str, Any],
        spec_response: requests.Response,
        png_bytes: bytes,
    ) -> None:
        def update() -> None:
            if result.matched:
                self.result_title_var.set(result.title or "(không rõ)")
                self.result_artist_var.set(result.artist or "(không rõ)")
                metrics = (
                    f"score = {result.score}    "
                    f"strength = {result.strength}    "
                    f"aligned = {result.aligned_fraction:.1%}    "
                    f"offset = {result.offset_seconds:.2f}s\n"
                    f"hashes = {result.query_hashes}    "
                    f"server time = {result.elapsed_ms} ms"
                )
                self.result_metrics_var.set(metrics)
            else:
                self.result_title_var.set("Không nhận ra")
                self.result_artist_var.set("—")
                self.result_metrics_var.set(
                    f"hashes = {result.query_hashes}    "
                    f"server time = {result.elapsed_ms} ms"
                )

            self._render_image(png_bytes)
            self._set_button_state()
            self._set_status("Xong.")
            self._set_response_text(
                self._build_response_blocks_for_success(
                    match_payload, spec_response, png_bytes
                )
            )

        self.root.after(0, update)

    @staticmethod
    def _build_response_blocks_for_success(
        match_payload: dict[str, Any],
        spec_response: requests.Response,
        png_bytes: bytes,
    ) -> str:
        match_block = (
            "POST /api/match\n"
            "  status: 200\n"
            "  content-type: application/json\n\n"
            + json.dumps(match_payload, indent=2, ensure_ascii=False)
        )
        spec_block = (
            "POST /api/spectrogram\n"
            f"  status: {spec_response.status_code}\n"
            f"  content-type: {spec_response.headers.get('content-type', '(none)')}\n"
            f"  content-length: {len(png_bytes)} bytes\n\n"
            f"<{len(png_bytes)} bytes of PNG — mở spectrogram ở trên để xem>"
        )
        return f"{match_block}\n\n{'-' * 60}\n\n{spec_block}"

    def _render_image(self, png_bytes: bytes) -> None:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        # Fit the image into the label while keeping aspect ratio. The
        # label reports its current size; we cache the widget reference so
        # we can read it inside the main-thread update.
        self.root.update_idletasks()
        max_width = max(self.image_label.winfo_width() - 8, 200)
        max_height = max(self.image_label.winfo_height() - 8, 200)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        self._current_image = ImageTk.PhotoImage(image)
        self.image_label.config(image=self._current_image, text="")

    def _show_error(self, title: str, body: str) -> None:
        def update() -> None:
            self._set_button_state()
            self._set_status("Lỗi.")
            self.result_title_var.set("—")
            self.result_artist_var.set("—")
            self.result_metrics_var.set("—")
            self.image_label.config(image="", text="(Lỗi)")
            self._current_image = None
            messagebox.showerror(title, body or "(không có nội dung)")
        self.root.after(0, update)


@dataclass(frozen=True)
class _Config:
    """Resolved configuration shared by the app.

    The base URL is sourced from three places, in order: the ``API_BASE_URL``
    environment variable, the project ``.env`` file, and finally the hard
    default. A small dataclass keeps the resolution out of ``main`` and
    avoids mutating module-level globals.
    """

    api_base: str


def _merge_dotenv(path: Path, base: str) -> str:
    """Read ``API_BASE_URL`` from ``path`` if present.

    Returns:
        The value from the file when ``API_BASE_URL`` is set, otherwise the
        unchanged ``base``.
    """
    if not path.exists():
        return base
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "API_BASE_URL":
            return value.strip().strip('"').strip("'")
    return base


def _resolve_config(project_root: Path) -> _Config:
    """Pick the API base URL from env, dotenv, or the default.

    Returns:
        A :class:`_Config` with the resolved base URL.
    """
    base = _merge_dotenv(project_root / ".env", DEFAULT_API_BASE)
    env_override = os.environ.get("API_BASE_URL")
    if env_override:
        base = env_override
    return _Config(api_base=base)


def main() -> None:
    config = _resolve_config(Path(__file__).resolve().parent.parent)
    root = tk.Tk()
    try:
        # Improve font rendering on Windows; the default Tk theme is serviceable
        # but looks dated alongside the rest of the project.
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    ShazamDesktopApp(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()
