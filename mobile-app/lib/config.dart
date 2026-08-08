/// App-wide constants for capture and transport.
///
/// Kept in one place so the desktop app (22050 Hz mono, 5–6 s), the
/// web client (worklet samples at device rate), and the matcher (built
/// at 22050 Hz) all agree on the wire format. We do not resample on the
/// client — see `docs/kien-truc.md` design decision #1.
library;

/// Sample rate baked into the matcher. Must match the corpus build.
const int sampleRate = 22050;

/// Mono capture. Stereo would double the payload for no matcher gain.
const int channels = 1;

/// How long a single tap records. Six seconds gives the matcher enough
/// hashes for a confident score (the server enforces a 1 s floor;
/// anything under ~3 s is reliably weak).
const Duration recordDuration = Duration(seconds: 6);

/// Match the desktop + web upload ceiling so error messages stay
/// consistent across clients.
const int maxUploadBytes = 10 * 1024 * 1024; // 10 MB

/// HTTP timeout for both `/api/match` and `/api/spectrogram`. Generous
/// because spectrogram rendering fans out to matplotlib on the server.
const Duration httpTimeout = Duration(seconds: 30);

/// The base URL of the FastAPI backend. Override at build time, e.g.:
///
///   flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000
///
/// Defaults that match each platform's local-host shortcut:
///   - Android emulator:  10.0.2.2 maps to the host machine
///   - iOS simulator / desktop:  127.0.0.1
///
/// The user can still override per-session in the API URL field on the
/// home screen.
const String defaultApiBase = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://10.0.2.2:8000',
);

/// Endpoints, kept here so the rest of the app does not hard-code
/// strings. Both routes are defined in `src/server/routes.py`.
const String matchPath = '/api/match';
const String spectrogramPath = '/api/spectrogram';

/// Multipart field name. Matches the FastAPI `UploadFile` parameter on
/// both endpoints.
const String uploadFieldName = 'file';

/// Filename sent in the multipart part. Arbitrary; the server only
/// inspects bytes.
const String uploadFileName = 'query.wav';
