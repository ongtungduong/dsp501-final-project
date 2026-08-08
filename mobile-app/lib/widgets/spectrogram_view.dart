import 'dart:typed_data';

import 'package:flutter/material.dart';

/// Render a PNG byte payload returned by `/api/spectrogram`. The bytes
/// are decoded once per `bytes` change — pass a new list to refresh.
///
/// We use `Image.memory` (decoded synchronously) rather than caching
/// the PNG to disk: a single spectrogram is small (~100 KB) and the
/// memory cost is short-lived. Revoking `ImageStream` is handled by
/// the framework when the widget unmounts.
class SpectrogramView extends StatelessWidget {
  const SpectrogramView({super.key, required this.bytes, this.height = 240});

  final List<int>? bytes;
  final double height;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final placeholder = Container(
      height: height,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        bytes == null ? '(Chưa có spectrogram)' : 'Đang tải…',
        style: theme.textTheme.bodyMedium?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
        ),
      ),
    );

    final data = bytes;
    if (data == null) return placeholder;

    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: Image.memory(
          Uint8List.fromList(data),
          fit: BoxFit.contain,
          gaplessPlayback: true,
          errorBuilder: (_, __, ___) => placeholder,
        ),
      ),
    );
  }
}
