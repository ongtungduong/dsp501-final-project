import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// One row in the API log — a timestamped HTTP request/response pair.
class ApiLogEntry {
  ApiLogEntry({
    required this.endpoint,
    required this.status,
    required this.body,
    DateTime? timestamp,
  }) : timestamp = timestamp ?? DateTime.now();

  final String endpoint;
  final int status;
  final String body;
  final DateTime timestamp;

  Color statusColor(ColorScheme scheme) {
    if (status == 0) return scheme.error;
    if (status >= 200 && status < 300) return scheme.primary;
    if (status >= 400) return scheme.error;
    return scheme.tertiary;
  }
}

/// Collapsible panel under the result card. Newest entry on top, capped
/// at `_maxEntries` so a long debug session does not bloat memory.
class ApiLogPanel extends StatelessWidget {
  const ApiLogPanel({
    super.key,
    required this.entries,
    required this.onClear,
  });

  static const int _maxEntries = 50;
  static const int maxEntries = _maxEntries;

  final List<ApiLogEntry> entries;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Theme(
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 16),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          leading: Icon(Icons.terminal, color: theme.colorScheme.primary),
          title: Text(
            'API Log (${entries.length})',
            style: theme.textTheme.titleMedium,
          ),
          subtitle: entries.isEmpty
              ? const Text('Chưa có request nào.')
              : Text(
                  entries.first.endpoint,
                  style: theme.textTheme.bodySmall,
                ),
          trailing: entries.isEmpty
              ? null
              : IconButton(
                  tooltip: 'Xoá log',
                  icon: const Icon(Icons.delete_outline),
                  onPressed: onClear,
                ),
          children: [
            if (entries.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  'Bấm nút ghi âm để xem request/response ở đây.',
                  style: theme.textTheme.bodySmall,
                ),
              )
            else
              ...entries.map((e) => _LogRow(entry: e)),
          ],
        ),
      ),
    );
  }
}

class _LogRow extends StatelessWidget {
  const _LogRow({required this.entry});

  final ApiLogEntry entry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final time =
        '${entry.timestamp.hour.toString().padLeft(2, '0')}:'
        '${entry.timestamp.minute.toString().padLeft(2, '0')}:'
        '${entry.timestamp.second.toString().padLeft(2, '0')}';

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: entry.statusColor(theme.colorScheme).withValues(alpha: 0.4),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  time,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: entry.statusColor(theme.colorScheme),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    entry.status == 0 ? 'NET ERR' : '${entry.status}',
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onPrimary,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    entry.endpoint,
                    style: theme.textTheme.labelMedium,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                IconButton(
                  tooltip: 'Copy',
                  icon: const Icon(Icons.copy, size: 16),
                  onPressed: () async {
                    await Clipboard.setData(
                      ClipboardData(
                        text: '[$time] ${entry.status} ${entry.endpoint}\n'
                            '${entry.body}',
                      ),
                    );
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text('Đã copy log vào clipboard.'),
                          duration: Duration(seconds: 2),
                        ),
                      );
                    }
                  },
                ),
              ],
            ),
            const SizedBox(height: 8),
            SelectableText(
              entry.body,
              style: theme.textTheme.bodySmall?.copyWith(
                fontFamily: 'monospace',
                height: 1.4,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
