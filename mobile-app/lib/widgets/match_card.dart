import 'package:flutter/material.dart';

import '../models/match_result.dart';

/// Render the result of one match call. Two distinct layouts:
///   * `match == null`  — server returned an honest non-match (HTTP 200)
///   * `match != null`  — recognised track, show title + artist + score
class MatchCard extends StatelessWidget {
  const MatchCard({super.key, required this.response});

  final MatchResponse response;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final match = response.match;

    final title = match == null ? 'Không nhận ra' : (match.title.isEmpty ? '(không rõ)' : match.title);
    final artist = match == null ? '—' : (match.artist ?? '(không rõ)');

    return Card(
      elevation: 0,
      color: theme.colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _Row(label: 'Bài hát', value: title, emphasis: true),
            const SizedBox(height: 6),
            _Row(label: 'Nghệ sĩ', value: artist),
            const SizedBox(height: 6),
            _Row(label: 'Độ đo', value: _metricsLine(match)),
            const SizedBox(height: 6),
            _Row(
              label: 'Hashes',
              value: '${response.queryHashes}    '
                  'server = ${response.elapsedMs} ms',
            ),
          ],
        ),
      ),
    );
  }

  String _metricsLine(MatchInfo? match) {
    if (match == null) return '—';
    return 'score = ${match.score}    '
        '${strengthLabel(match.strength)}    '
        'aligned = ${(match.alignedFraction * 100).toStringAsFixed(1)}%    '
        'offset = ${match.offsetSeconds.toStringAsFixed(2)}s';
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.value, this.emphasis = false});

  final String label;
  final String value;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 88,
          child: Text(
            '$label:',
            style: theme.textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontSize: emphasis ? 16 : 14,
              fontWeight: emphasis ? FontWeight.w600 : FontWeight.w400,
            ),
          ),
        ),
      ],
    );
  }
}
