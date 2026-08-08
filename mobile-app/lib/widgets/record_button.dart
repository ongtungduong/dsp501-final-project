import 'package:flutter/material.dart';

import '../config.dart';

/// Single button, three states. Mirrors the desktop app's button
/// (ready / recording / busy) so a tester moving between the two
/// clients does not have to relearn the affordance.
enum RecordButtonState { ready, recording, busy }

class RecordButton extends StatelessWidget {
  const RecordButton({
    super.key,
    required this.state,
    required this.onPressed,
    this.duration = recordDuration,
  });

  final RecordButtonState state;
  final VoidCallback? onPressed;
  final Duration duration;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final isReady = state == RecordButtonState.ready;

    final (label, color, fg) = switch (state) {
      RecordButtonState.ready => (
          'Bắt đầu nhận diện',
          scheme.primary,
          scheme.onPrimary,
        ),
      RecordButtonState.recording => (
          'Đang nghe… (${duration.inSeconds}s)',
          scheme.error,
          scheme.onError,
        ),
      RecordButtonState.busy => (
          'Đang xử lý…',
          scheme.secondary,
          scheme.onSecondary,
        ),
    };

    return SizedBox(
      width: double.infinity,
      height: 56,
      child: FilledButton(
        onPressed: isReady ? onPressed : null,
        style: FilledButton.styleFrom(
          backgroundColor: color,
          foregroundColor: fg,
          disabledBackgroundColor: color.withOpacity(0.6),
          disabledForegroundColor: fg.withOpacity(0.7),
        ),
        child: Text(
          label,
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
    );
  }
}
