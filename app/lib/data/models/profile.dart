class UserProfile {
  const UserProfile({
    required this.id,
    required this.name,
    required this.email,
    required this.avatarUrl,
    required this.title,
    required this.level,
    this.travelPersonalities = const [],
    this.travelRhythm,
    this.pushNotificationsEnabled = true,
  });

  final String id;
  final String name;
  final String email;
  final String avatarUrl;

  /// e.g. "Global Explorer".
  final String title;
  final int level;

  /// Selections from onboarding, e.g. ["Museum Maven", "Wild Nomad"].
  final List<String> travelPersonalities;

  /// e.g. "Spontaneous".
  final String? travelRhythm;
  final bool pushNotificationsEnabled;

  UserProfile copyWith({
    String? name,
    String? email,
    bool? pushNotificationsEnabled,
  }) {
    return UserProfile(
      id: id,
      name: name ?? this.name,
      email: email ?? this.email,
      avatarUrl: avatarUrl,
      title: title,
      level: level,
      travelPersonalities: travelPersonalities,
      travelRhythm: travelRhythm,
      pushNotificationsEnabled:
          pushNotificationsEnabled ?? this.pushNotificationsEnabled,
    );
  }

  /// Wipes AI personalization (travel personalities/rhythm) — used by the
  /// Profile screen's "Reset & Start Over" action.
  UserProfile resetPersonalization() {
    return UserProfile(
      id: id,
      name: name,
      email: email,
      avatarUrl: avatarUrl,
      title: title,
      level: level,
      pushNotificationsEnabled: pushNotificationsEnabled,
    );
  }
}

/// One of the "travel soul" archetypes offered during onboarding.
class TravelPersonality {
  const TravelPersonality({
    required this.title,
    required this.description,
    required this.icon,
  });

  final String title;
  final String description;
  final String icon;
}
