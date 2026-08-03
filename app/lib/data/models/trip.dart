/// Lifecycle of a trip — drives which screen/section it surfaces in
/// (Home's "Past Trips", the live Active Trip Explorer, etc).
enum TripStatus { upcoming, active, completed }

class Trip {
  const Trip({
    required this.id,
    required this.destination,
    required this.country,
    required this.coverImageUrl,
    required this.startDate,
    required this.endDate,
    required this.status,
    required this.category,
    required this.days,
    this.progress = 0,
    this.accommodation,
    this.foodSpots = const [],
    this.itinerary = const [],
    this.liveTimeline = const [],
  });

  final String id;
  final String destination;
  final String country;
  final String coverImageUrl;
  final DateTime startDate;
  final DateTime endDate;
  final TripStatus status;

  /// e.g. "Relaxation", "Culture", "Adventure" — shown as a tag pill.
  final String category;
  final int days;

  /// 0.0–1.0, only meaningful while [status] is [TripStatus.active].
  final double progress;

  final Accommodation? accommodation;
  final List<FoodSpot> foodSpots;
  final List<ItineraryDay> itinerary;

  /// "Today's" schedule with per-activity status, powering the Active Trip
  /// Explorer. Only populated when [status] is [TripStatus.active].
  final List<ItineraryActivity> liveTimeline;
}

class Accommodation {
  const Accommodation({
    required this.name,
    required this.imageUrl,
    required this.description,
  });

  final String name;
  final String imageUrl;
  final String description;
}

class FoodSpot {
  const FoodSpot({
    required this.name,
    required this.imageUrl,
    required this.description,
  });

  final String name;
  final String imageUrl;
  final String description;
}

class ItineraryDay {
  const ItineraryDay({
    required this.dayNumber,
    required this.title,
    required this.subtitle,
    required this.activities,
  });

  final int dayNumber;
  final String title;
  final String subtitle;
  final List<ItineraryActivity> activities;
}

/// Status of a single activity within the *currently active* trip's live
/// timeline (Active Trip Explorer). Past/future trips only ever use
/// [ActivityStatus.upcoming] since the concept doesn't apply to them.
enum ActivityStatus { completed, active, upcoming }

class ItineraryActivity {
  const ItineraryActivity({
    required this.time,
    required this.title,
    required this.description,
    required this.icon,
    this.imageUrl,
    this.status = ActivityStatus.upcoming,
    this.bookingReference,
  });

  final String time;
  final String title;
  final String description;

  /// Material icon codepoint name, e.g. "sailing", "restaurant" — kept as a
  /// string so it maps 1:1 to a future API payload instead of a Flutter type.
  final String icon;
  final String? imageUrl;
  final ActivityStatus status;
  final String? bookingReference;
}
