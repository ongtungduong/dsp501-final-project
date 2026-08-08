/// HTTP client for the two endpoints the mobile app talks to:
///
///   POST /api/match         — multipart upload of a WAV file
///   POST /api/spectrogram   — same payload, returns `image/png`
///
/// Both endpoints are defined in `src/server/routes.py` and decode
/// WAV through the same signal path as the desktop + web clients.
/// Errors carry Vietnamese messages so the UI does not have to.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../config.dart';
import '../models/match_result.dart';

/// Thrown when an API call fails. `status == 0` means we never reached
/// the server (DNS, refused connection, offline, timeout).
class ApiError implements Exception {
  ApiError(this.message, this.status);

  final String message;
  final int status;

  @override
  String toString() => 'ApiError($status): $message';
}

class ApiClient {
  ApiClient({http.Client? inner, String? baseUrl})
    : _inner = inner ?? http.Client(),
      _baseUrl = baseUrl ?? defaultApiBase;

  final http.Client _inner;
  String _baseUrl;

  /// The currently configured base URL, with trailing slash stripped.
  /// Mutable so the UI's API URL field can update it on the fly.
  String get baseUrl => _baseUrl;

  set baseUrl(String value) {
    var trimmed = value.trim();
    while (trimmed.endsWith('/')) {
      trimmed = trimmed.substring(0, trimmed.length - 1);
    }
    _baseUrl = trimmed;
  }

  /// POST `wavBytes` to `/api/match`.
  ///
  /// On success, returns the parsed [MatchResponse]. The server returns
  /// HTTP 200 even when no song matched — `match` will be `null` in
  /// that case. The HTTP layer only throws for non-200 responses or
  /// network failures.
  Future<MatchResponse> matchAudio(List<int> wavBytes) async {
    final uri = Uri.parse('$_baseUrl$matchPath');
    final response = await _sendMultipart(uri, wavBytes);
    return MatchResponse.fromJson(_decodeJson(response));
  }

  /// POST the same WAV to `/api/spectrogram`. Returns the PNG bytes —
  /// the caller renders them with `Image.memory`.
  Future<List<int>> getSpectrogram(List<int> wavBytes) async {
    final uri = Uri.parse('$_baseUrl$spectrogramPath');
    final streamed = await _sendStreamedMultipart(uri, wavBytes);
    if (streamed.statusCode != 200) {
      final body = await streamed.stream.bytesToString();
      throw ApiError(
        _fallbackForStatus(streamed.statusCode, body),
        streamed.statusCode,
      );
    }
    return streamed.stream.toBytes();
  }

  /// Build + send a multipart request, buffering the response body so
  /// the JSON parser can run against it.
  Future<http.Response> _sendMultipart(Uri uri, List<int> wavBytes) async {
    final request = http.MultipartRequest('POST', uri)
      ..files.add(
        http.MultipartFile.fromBytes(
          uploadFieldName,
          wavBytes,
          filename: uploadFileName,
        ),
      );

    final http.StreamedResponse streamed;
    try {
      streamed = await _inner.send(request).timeout(httpTimeout);
    } on TimeoutException {
      throw ApiError('Máy chủ không phản hồi, kiểm tra API URL và mạng LAN.', 0);
    } on SocketException {
      throw ApiError('Không kết nối được máy chủ, kiểm tra backend đã chạy chưa.', 0);
    } on http.ClientException {
      throw ApiError('Không kết nối được máy chủ, kiểm tra backend đã chạy chưa.', 0);
    }

    final body = await streamed.stream.bytesToString();
    final response = http.Response(
      body,
      streamed.statusCode,
      headers: streamed.headers,
    );
    if (response.statusCode != 200) {
      throw ApiError(_extractDetail(response), response.statusCode);
    }
    return response;
  }

  /// Same as [_sendMultipart] but returns the streamed response so the
  /// caller can read binary PNG bytes directly.
  Future<http.StreamedResponse> _sendStreamedMultipart(
    Uri uri,
    List<int> wavBytes,
  ) async {
    final request = http.MultipartRequest('POST', uri)
      ..files.add(
        http.MultipartFile.fromBytes(
          uploadFieldName,
          wavBytes,
          filename: uploadFileName,
        ),
      );

    try {
      return await _inner.send(request).timeout(httpTimeout);
    } on TimeoutException {
      throw ApiError('Máy chủ không phản hồi khi vẽ spectrogram.', 0);
    } on SocketException {
      throw ApiError('Không kết nối được máy chủ khi vẽ spectrogram.', 0);
    } on http.ClientException {
      throw ApiError('Không kết nối được máy chủ khi vẽ spectrogram.', 0);
    }
  }

  Map<String, dynamic> _decodeJson(http.Response response) {
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, dynamic>) return decoded;
      throw ApiError('Phản hồi từ server không đúng định dạng.', response.statusCode);
    } on FormatException {
      throw ApiError('Phản hồi từ server không phải JSON.', response.statusCode);
    }
  }

  /// Pull the `detail` field out of FastAPI's standard error envelope.
  /// Falls back to a Vietnamese message per status when the body is
  /// missing or malformed.
  String _extractDetail(http.Response response) {
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map &&
          decoded['detail'] is String &&
          (decoded['detail'] as String).isNotEmpty) {
        return decoded['detail'] as String;
      }
    } on FormatException {
      // Body was not JSON — fall through to the fallback.
    }
    return _fallbackForStatus(response.statusCode, response.body);
  }

  String _fallbackForStatus(int status, String body) {
    if (status == 413) return 'Đoạn ghi âm quá lớn để tải lên.';
    if (status == 422) {
      // Server always sets a Vietnamese `detail`; this is only a safety
      // net for an unexpected empty body.
      return 'Âm thanh bị lỗi hoặc quá ngắn (dưới 1 giây).';
    }
    if (body.isNotEmpty) return 'Máy chủ trả lỗi $status: $body';
    return 'Máy chủ trả lỗi $status.';
  }
}
