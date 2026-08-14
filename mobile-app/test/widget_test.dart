import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shazam_mobile/widgets/record_button.dart';

/// [RecordButton] is the one widget that gates the whole recognition
/// flow, and it is pure: no plugins, no network, no platform channels.
/// That makes it the piece worth covering here — the screen around it
/// needs the microphone and HTTP plugins, which a widget test cannot
/// provide without mocks.
Widget _wrap(Widget child) => MaterialApp(home: Scaffold(body: child));

void main() {
  testWidgets('is tappable and labelled for recognition when ready',
      (tester) async {
    var taps = 0;
    await tester.pumpWidget(
      _wrap(
        RecordButton(
          state: RecordButtonState.ready,
          onPressed: () => taps++,
        ),
      ),
    );

    expect(find.text('Bắt đầu nhận diện'), findsOneWidget);

    await tester.tap(find.byType(FilledButton));
    expect(taps, 1);
  });

  testWidgets('shows the clip length while recording', (tester) async {
    await tester.pumpWidget(
      _wrap(
        const RecordButton(
          state: RecordButtonState.recording,
          onPressed: null,
          duration: Duration(seconds: 6),
        ),
      ),
    );

    expect(find.text('Đang nghe… (6s)'), findsOneWidget);
  });

  testWidgets('refuses presses while recording', (tester) async {
    await tester.pumpWidget(
      _wrap(
        RecordButton(
          state: RecordButtonState.recording,
          onPressed: () {},
        ),
      ),
    );

    // A disabled FilledButton carries a null callback, so asserting on
    // the widget is deterministic where tapping would depend on hit
    // testing a disabled surface.
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
  });

  testWidgets('refuses presses while busy', (tester) async {
    await tester.pumpWidget(
      _wrap(
        RecordButton(
          state: RecordButtonState.busy,
          onPressed: () {},
        ),
      ),
    );

    expect(find.text('Đang xử lý…'), findsOneWidget);
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
  });
}
