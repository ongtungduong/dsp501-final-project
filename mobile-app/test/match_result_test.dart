import 'package:flutter_test/flutter_test.dart';
import 'package:shazam_mobile/models/match_result.dart';

void main() {
  group('MatchResponse.fromJson', () {
    test('parses an honest non-match (match=null)', () {
      final json = <String, dynamic>{
        'match': null,
        'queryHashes': 42,
        'elapsedMs': 87,
      };

      final response = MatchResponse.fromJson(json);

      expect(response.match, isNull);
      expect(response.queryHashes, 42);
      expect(response.elapsedMs, 87);
    });

    test('parses a matched track with all fields', () {
      final json = <String, dynamic>{
        'match': {
          'songId': 17,
          'title': 'Imagine',
          'artist': 'John Lennon',
          'score': 240,
          'alignedFraction': 0.184,
          'strength': 'strong',
          'offsetSeconds': 1.23,
        },
        'queryHashes': 310,
        'elapsedMs': 142,
      };

      final response = MatchResponse.fromJson(json);

      expect(response.match, isNotNull);
      final m = response.match!;
      expect(m.songId, 17);
      expect(m.title, 'Imagine');
      expect(m.artist, 'John Lennon');
      expect(m.score, 240);
      expect(m.alignedFraction, closeTo(0.184, 1e-9));
      expect(m.strength, MatchStrength.strong);
      expect(m.offsetSeconds, closeTo(1.23, 1e-9));
      expect(response.queryHashes, 310);
      expect(response.elapsedMs, 142);
    });

    test('accepts null artist', () {
      final json = <String, dynamic>{
        'match': {
          'songId': 1,
          'title': 'Unknown',
          'artist': null,
          'score': 10,
          'alignedFraction': 0.02,
          'strength': 'weak',
          'offsetSeconds': 0.0,
        },
        'queryHashes': 5,
        'elapsedMs': 30,
      };

      final m = MatchResponse.fromJson(json).match!;
      expect(m.artist, isNull);
      expect(m.strength, MatchStrength.weak);
    });

    test('coerces numeric fields from int', () {
      final json = <String, dynamic>{
        'match': {
          'songId': 1,
          'title': 'Track',
          'artist': 'A',
          'score': 5,
          'alignedFraction': 0, // int, not double
          'strength': 'moderate',
          'offsetSeconds': 0, // int, not double
        },
        'queryHashes': 1,
        'elapsedMs': 1,
      };

      final m = MatchResponse.fromJson(json).match!;
      expect(m.alignedFraction, 0.0);
      expect(m.offsetSeconds, 0.0);
      expect(m.strength, MatchStrength.moderate);
    });

    test('rejects an unknown strength', () {
      final json = <String, dynamic>{
        'match': {
          'songId': 1,
          'title': 'X',
          'artist': 'Y',
          'score': 0,
          'alignedFraction': 0.0,
          'strength': 'definitely',
          'offsetSeconds': 0.0,
        },
        'queryHashes': 0,
        'elapsedMs': 0,
      };

      expect(() => MatchResponse.fromJson(json), throwsFormatException);
    });

    test('rejects missing required field', () {
      final json = <String, dynamic>{
        'match': {
          'songId': 1,
          // title missing
          'artist': null,
          'score': 0,
          'alignedFraction': 0.0,
          'strength': 'weak',
          'offsetSeconds': 0.0,
        },
        'queryHashes': 0,
        'elapsedMs': 0,
      };

      expect(() => MatchResponse.fromJson(json), throwsA(isA<TypeError>()));
    });
  });

  group('strengthLabel', () {
    test('renders Vietnamese labels', () {
      expect(strengthLabel(MatchStrength.strong), 'Khớp chắc');
      expect(strengthLabel(MatchStrength.moderate), 'Khớp vừa');
      expect(strengthLabel(MatchStrength.weak), 'Khớp yếu');
    });
  });
}
