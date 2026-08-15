/// Thin wrapper around the `record` plugin.
///
/// Captures a single mono clip at [sampleRate] Hz, encoded as WAV PCM
/// 16-bit (the format the desktop app uses too — same on-the-wire
/// container means the server's `soundfile` decoder behaves identically
/// across all clients). The recording is bounded by [recordDuration]
/// via the caller's `Future.delayed`, not by this class — that lets
/// the home screen show a countdown while audio is in flight.
library;

import 'dart:io';

import 'package:flutter/services.dart' show MissingPluginException, PlatformException;
import 'package:path_provider/path_provider.dart' as path_provider;
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

import '../config.dart';

/// Thrown when the microphone is unavailable or the user denied access.
class RecorderError implements Exception {
  RecorderError(this.message);
  final String message;
  @override
  String toString() => 'RecorderError: $message';
}

class RecorderService {
  RecorderService({AudioRecorder? recorder})
    : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;

  bool _isRecording = false;
  String? _currentPath;

  bool get isRecording => _isRecording;

  /// Request microphone permission. Returns `true` when capture may
  /// proceed. The user can deny permanently — in that case we throw
  /// with a message that points them at the OS settings page.
  Future<bool> ensurePermission() async {
    final status = await Permission.microphone.request();
    if (status.isGranted) return true;
    if (status.isPermanentlyDenied) {
      throw RecorderError(
        'Quyền micro bị từ chối vĩnh viễn. Mở Cài đặt hệ thống để bật lại.',
      );
    }
    if (status.isRestricted) {
      throw RecorderError(
        'Quyền micro bị hạn chế bởi thiết bị (thường do MDM/emulator).',
      );
    }
    throw RecorderError('Cần cấp quyền micro để ghi âm.');
  }

  /// Start a recording that runs until [stop] is called. Returns the
  /// path the WAV file will land at — read it back via [stop].
  Future<String> start() async {
    if (_isRecording) {
      throw RecorderError('Đang ghi âm, không thể bắt đầu lại.');
    }

    // Permission probe. We use the plugin's own check first — it returns
    // false (rather than throwing) when the runtime permission is missing
    // on Android 6+, which is the path we want to walk the user through.
    bool hasPermission;
    try {
      hasPermission = await _recorder.hasPermission();
    } catch (e) {
      // Some emulator builds throw on the probe. Treat as "denied" and
      // fall through to ensurePermission(), which surfaces a Vietnamese
      // message rather than a stack trace.
      hasPermission = false;
    }
    if (!hasPermission) {
      await ensurePermission();
      // Re-check after the OS prompt completes. On emulator the prompt
      // may auto-grant or auto-deny without user interaction.
      hasPermission = await _recorder.hasPermission();
      if (!hasPermission) {
        throw RecorderError(
          'Không có quyền truy cập micro. Kiểm tra Cài đặt hệ thống hoặc cấp quyền thủ công: '
          'adb shell pm grant com.dsp501.shazam_mobile android.permission.RECORD_AUDIO',
        );
      }
    }

    // The plugin requires a real filesystem path on all IO platforms.
    // We stage the file in the app's temporary directory so the OS
    // reclaims it eventually. Both Android (path_provider_android) and
    // Chrome (path_provider_web) can fail when the plugin channel is
    // not registered — `path_provider` itself is on pub.dev but only the
    // platform-specific package gets bound to the engine. A MissingPlugin
    // is recoverable; a raw PlatformException usually means the build did
    // not include the platform plugin, so the catch-all surfaces a hint.
    final Directory dir;
    try {
      dir = await path_provider.getTemporaryDirectory();
    } on MissingPluginException {
      throw RecorderError(
        'Plugin path_provider chưa được đăng ký cho nền tảng này. '
        'Chạy lại `flutter pub get` rồi build lại APK.',
      );
    } on PlatformException catch (e) {
      throw RecorderError(
        'Không lấy được thư mục tạm: ${e.message ?? e.code}',
      );
    }
    final stamp = DateTime.now().millisecondsSinceEpoch;
    final path = '${dir.path}/shazam_query_$stamp.wav';

    // All three voice-processing effects stay OFF, matching the web
    // client and design decision #1 in docs/kien-truc.md. Each one is
    // signal processing applied before we ever see a sample, and each
    // breaks fingerprinting in its own way:
    //
    //   autoGain      changes gain over time, so the server's static
    //                 peak normalisation cannot undo it.
    //   noiseSuppress attenuates exactly the steady tonal components
    //                 that peak picking selects.
    //   echoCancel    suppresses audio correlated with what the device
    //                 is playing — which is the demo scenario itself,
    //                 a phone listening to music from a nearby speaker.
    //
    // These are tuned for speech, and music recognition is not speech.
    // Note that emulators usually drop the effects anyway, so testing
    // there would not have shown the difference either way.
    //
    // Sample rate and channel count drive the WAV header the server
    // decodes, so they MUST match the matcher build.
    const config = RecordConfig(
      encoder: AudioEncoder.wav,
      sampleRate: sampleRate,
      numChannels: channels,
      bitRate: 256000, // ignored for WAV but required by the API
      autoGain: false,
      echoCancel: false,
      noiseSuppress: false,
    );

    try {
      await _recorder.start(config, path: path);
    } catch (e) {
      // Surface the plugin's own message — on emulator it usually reads
      // "AudioRecord start failed" or "device busy". Translate the most
      // common variants so the UI does not leak a stack trace.
      final raw = e.toString();
      if (raw.contains('busy') || raw.contains('BUSY')) {
        throw RecorderError('Micro đang bận, thử lại sau vài giây.');
      }
      if (raw.contains('permission') || raw.contains('Permission')) {
        throw RecorderError(
          'Thiếu quyền micro. Cấp thủ công: adb shell pm grant '
          'com.dsp501.shazam_mobile android.permission.RECORD_AUDIO',
        );
      }
      throw RecorderError('Không mở được micro: $raw');
    }

    _currentPath = path;
    _isRecording = true;
    return path;
  }

  /// Stop the recording and return the WAV bytes. Throws if nothing
  /// was recorded or the resulting file is empty.
  Future<List<int>> stop() async {
    if (!_isRecording) {
      throw RecorderError('Chưa bắt đầu ghi âm.');
    }

    String? path;
    try {
      path = await _recorder.stop();
    } catch (e) {
      _isRecording = false;
      _currentPath = null;
      throw RecorderError('Lỗi khi dừng ghi âm: $e');
    }
    _isRecording = false;

    // The plugin returns the path it wrote to (or null if nothing was
    // captured). Fall back to the path we asked for, which is the same
    // thing on every IO platform that supports WAV.
    final resolvedPath = path ?? _currentPath;
    _currentPath = null;

    if (resolvedPath == null) {
      throw RecorderError('Không nhận được đường dẫn file từ plugin.');
    }

    final file = File(resolvedPath);
    if (!file.existsSync()) {
      throw RecorderError('File ghi âm không tồn tại: $resolvedPath');
    }
    final bytes = await file.readAsBytes();
    if (bytes.isEmpty) {
      throw RecorderError('File ghi âm rỗng.');
    }

    // Best-effort cleanup. If the platform keeps the file for the
    // upload, the OS reaps the temp dir later.
    try {
      await file.delete();
    } on FileSystemException {
      // Ignore — the upload already happened.
    }

    return bytes;
  }

  /// Stop and discard. Useful in error paths where we do not want to
  /// leak the temporary file.
  Future<void> cancel() async {
    if (!_isRecording) return;
    try {
      await _recorder.cancel();
    } catch (_) {
      // Swallow — the caller is already on the failure path.
    } finally {
      _isRecording = false;
      _currentPath = null;
    }
  }
}
