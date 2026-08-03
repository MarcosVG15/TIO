enum FriendStatus { activeNow, readyToTravel, away }

class Friend {
  const Friend({
    required this.id,
    required this.name,
    required this.avatarUrl,
    required this.status,
    this.statusDetail,
  });

  final String id;
  final String name;
  final String avatarUrl;
  final FriendStatus status;

  /// e.g. "Greece" when [status] is [FriendStatus.away].
  final String? statusDetail;
}

enum SocialPostType { photo, itinerary }

class SocialPost {
  const SocialPost({
    required this.id,
    required this.authorName,
    required this.authorAvatarUrl,
    required this.postedAt,
    required this.location,
    required this.type,
    this.imageUrl,
    this.caption,
    this.itineraryTitle,
    this.itinerarySteps = const [],
    this.likes = 0,
    this.comments = 0,
    this.contributorAvatarUrls = const [],
  });

  final String id;
  final String authorName;
  final String authorAvatarUrl;
  final DateTime postedAt;
  final String location;
  final SocialPostType type;

  // [SocialPostType.photo]
  final String? imageUrl;
  final String? caption;
  final int likes;
  final int comments;

  // [SocialPostType.itinerary]
  final String? itineraryTitle;
  final List<ItineraryStep> itinerarySteps;
  final List<String> contributorAvatarUrls;
}

class ItineraryStep {
  const ItineraryStep({required this.title, required this.subtitle});

  final String title;
  final String subtitle;
}
