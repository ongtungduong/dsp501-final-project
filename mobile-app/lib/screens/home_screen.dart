import 'dart:async';

import 'package:flutter/material.dart';

import '../config.dart';
import '../models/match_result.dart';
import '../services/api_client.dart';
import '../services/recorder_service.dart';
import '../widgets/match_card.dart';
import '../widgets/record_button.dart';
import '../widgets/spectrogram_view.dart';

/// Single-page UI: API URL field, record button, status line, result
/// card, spectrogram. Mirrors the desktop app's vertical stack so the
/// tester does not have to context-switch when comparing.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final ApiClient _api = ApiClient();
  final RecorderService _recorder = RecorderService();

  late final TextEditingController _apiUrlController;

  MatchResponse? _match;
  List<int>? _spectrogramBytes;
  String _status = 'Sẵn sàng.';
  RecordButtonState _buttonState = RecordButtonState.ready;

  Timer? _recordingTimer;

  @override
  void initState() {
    super.initState();
    _apiUrlController = TextEditingController(text: _api.baseUrl);
  }

  @override
  void dispose() {
    _recordingTimer?.cancel();
    _apiUrlController.dispose();
    super.dispose();
  }

  void _setApiUrl(String value) {
    _api.baseUrl = value;
  }

  Future<void> _onRecordPressed() async {
    if (_buttonState != RecordButtonState.ready) return;

    setState(() {
      _match = null;
      _spectrogramBytes = null;
      _status = 'Đang mở micro…';
      _buttonState = RecordButtonState.recording;
    });

    try {
      await _recorder.start();
    } on RecorderError catch (e) {
      // Critical: reset ALL state, not just status/button. Otherwise
      // a stale `_match` from a previous run lingers after the user
      // sees the error toast, which is confusing.
      if (!mounted) return;
      setState(() {
        _status = e.message;
        _buttonState = RecordButtonState.ready;
        _match = null;
        _spectrogramBytes = null;
      });
      _showError('Lỗi micro', e.message);
      return;
    } catch (e, st) {
      debugPrint('Unexpected recorder error: $e\n$st');
      if (!mounted) return;
      setState(() {
        _status = e.toString();
        _buttonState = RecordButtonState.ready;
        _match = null;
        _spectrogramBytes = null;
      });
      _showError('Lỗi không mong đợi', e.toString());
      return;
    }

    if (!mounted) return;
    _setStatus('Đang nghe…');
    _recordingTimer = Timer(recordDuration, _onRecordingElapsed);
  }

  Future<void> _onRecordingElapsed() async {
    _recordingTimer?.cancel();
    _recordingTimer = null;

    if (!mounted) return;
    setState(() {
      _buttonState = RecordButtonState.busy;
      _status = 'Đang gửi lên server…';
    });

    final List<int> wavBytes;
    try {
      wavBytes = await _recorder.stop();
    } on RecorderError catch (e) {
      _showError('Lỗi ghi âm', e.message);
      return;
    } catch (e, st) {
      debugPrint('Unexpected stop error: $e\n$st');
      _showError('Lỗi không mong đợi', e.toString());
      return;
    }

    if (wavBytes.isEmpty) {
      _showError('Không có dữ liệu', 'Micro không trả về dữ liệu.');
      return;
    }

    try {
      final match = await _api.matchAudio(wavBytes);
      if (!mounted) return;
      setState(() {
        _match = match;
        _status = match.match == null ? 'Không nhận ra bài nào.' : 'Xong.';
      });

      // Spectrogram is purely cosmetic — do not fail the whole flow if
      // it errors out. The match verdict is already on screen.
      try {
        final png = await _api.getSpectrogram(wavBytes);
        if (!mounted) return;
        setState(() => _spectrogramBytes = png);
      } on ApiError catch (e) {
        if (!mounted) return;
        _setStatus('Đã nhận diện nhưng lỗi khi tải spectrogram: ${e.message}');
      }
    } on ApiError catch (e) {
      _showError('Lỗi máy chủ', e.message);
    } catch (e, st) {
      debugPrint('Unexpected match error: $e\n$st');
      _showError('Lỗi không mong đợi', e.toString());
    }
  }

  void _showError(String title, String body) {
    if (!mounted) return;
    setState(() {
      _status = body;
      _buttonState = RecordButtonState.ready;
    });
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text('$title: $body'),
          duration: const Duration(seconds: 4),
        ),
      );
  }

  void _setStatus(String value) {
    if (!mounted) return;
    setState(() => _status = value);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Shazam — Nhận diện âm thanh'),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _ApiUrlField(
                controller: _apiUrlController,
                enabled: _buttonState == RecordButtonState.ready,
                onChanged: _setApiUrl,
              ),
              const SizedBox(height: 12),
              RecordButton(
                state: _buttonState,
                onPressed: _onRecordPressed,
              ),
              const SizedBox(height: 8),
              Text(
                _status,
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 16),
              if (_match != null) ...[
                Text(
                  'Kết quả',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                MatchCard(response: _match!),
                const SizedBox(height: 16),
                Text(
                  'Spectrogram',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                SpectrogramView(bytes: _spectrogramBytes),
              ] else
                const _EmptyState(),
            ],
          ),
        ),
      ),
    );
  }
}

class _ApiUrlField extends StatelessWidget {
  const _ApiUrlField({
    required this.controller,
    required this.enabled,
    required this.onChanged,
  });

  final TextEditingController controller;
  final bool enabled;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      enabled: enabled,
      onChanged: onChanged,
      decoration: const InputDecoration(
        labelText: 'API URL',
        hintText: 'http://192.168.1.20:8000',
        border: OutlineInputBorder(),
        isDense: true,
      ),
      keyboardType: TextInputType.url,
      autocorrect: false,
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 48),
      alignment: Alignment.center,
      child: Column(
        children: [
          Icon(
            Icons.music_note_outlined,
            size: 48,
            color: theme.colorScheme.onSurfaceVariant,
          ),
          const SizedBox(height: 12),
          Text(
            'Bấm nút phía trên để thu 6 giây và nhận diện.',
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}
