/// Typed view of the JSON returned by `POST /api/match`.
///
/// The server uses Pydantic's `to_camel` alias generator, so the wire
/// shape is camelCase. Parsing happens with explicit null checks and
/// fall-throughs rather than `fromJson` codegen: the surface area is
/// small and the extra dependency is not worth it.
library;

/// Verdict categories, mirrors `MatchStrength` on the server side.
enum MatchStrength { strong, moderate, weak }

/// The recognised track, when the query was found in the corpus.
class MatchInfo {
  const MatchInfo({
    required this.songId,
    required this.title,
    required this.artist,
    required this.score,
    required this.alignedFraction,
    required this.strength,
    required this.offsetSeconds,
  });

  final int songId;
  final String title;

  /// `null` when the catalogue row had no artist.
  final String? artist;
  final int score;

  /// NOT a confidence percentage. ~0.18 for a flawless match, ~0.02 for
  /// a real room recording. Render `strength` instead for the verdict.
  final double alignedFraction;
  final MatchStrength strength;
  final double offsetSeconds;

  /// Build from the nested `match` object on the response payload.
  /// Throws [FormatException] when a required field is missing or has
  /// the wrong type — that is a server-contract bug, not user input.
  factory MatchInfo.fromJson(Map<String, dynamic> json) {
    final strengthStr = json['strength'];
    return MatchInfo(
      songId: json['songId'] as int,
      title: json['title'] as String,
      artist: json['artist'] as String?,
      score: json['score'] as int,
      alignedFraction: (json['alignedFraction'] as num).toDouble(),
      strength: _strengthFromJson(strengthStr),
      offsetSeconds: (json['offsetSeconds'] as num).toDouble(),
    );
  }
}

MatchStrength _strengthFromJson(Object? raw) {
  switch (raw) {
    case 'strong':
      return MatchStrength.strong;
    case 'moderate':
      return MatchStrength.moderate;
    case 'weak':
      return MatchStrength.weak;
    default:
      throw FormatException('Unknown match strength: $raw');
  }
}

String strengthLabel(MatchStrength s) {
  switch (s) {
    case MatchStrength.strong:
      return 'Khớp chắc';
    case MatchStrength.moderate:
      return 'Khớp vừa';
    case MatchStrength.weak:
      return 'Khớp yếu';
  }
}

/// Body of `POST /api/match`. `match` is `null` on an honest non-match
/// (HTTP 200 — see `src/server/routes.py::match_audio`).
class MatchResponse {
  const MatchResponse({
    required this.match,
    required this.queryHashes,
    required this.elapsedMs,
  });

  final MatchInfo? match;
  final int queryHashes;
  final int elapsedMs;

  factory MatchResponse.fromJson(Map<String, dynamic> json) {
    final rawMatch = json['match'];
    return MatchResponse(
      match: rawMatch == null
          ? null
          : MatchInfo.fromJson(rawMatch as Map<String, dynamic>),
      queryHashes: json['queryHashes'] as int,
      elapsedMs: json['elapsedMs'] as int,
    );
  }

  /// Reverse of [fromJson] with camelCase keys so the log panel can
  /// pretty-print what the server actually returned.
  Map<String, dynamic> toJson() => {
    'match': match == null
        ? null
        : {
            'songId': match!.songId,
            'title': match!.title,
            'artist': match!.artist,
            'score': match!.score,
            'alignedFraction': match!.alignedFraction,
            'strength': match!.strength.name,
            'offsetSeconds': match!.offsetSeconds,
          },
    'queryHashes': queryHashes,
    'elapsedMs': elapsedMs,
  };
}
