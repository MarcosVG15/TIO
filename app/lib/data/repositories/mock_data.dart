import '../../core/widgets/app_image.dart';
import '../models/map_pin.dart';
import '../models/profile.dart';
import '../models/recommendation.dart';
import '../models/social.dart';
import '../models/trip.dart';

/// Static, in-memory stand-in for a future backend. Every repository reads
/// from here — once a real API/database exists, only the repository
/// implementations change, not this shape of data.
class MockData {
  MockData._();

  static final trips = <Trip>[
    Trip(
      id: 'santorini',
      destination: 'Santorini Getaway',
      country: 'Greece',
      coverImageUrl: DummyImages.santorini,
      startDate: DateTime(2026, 7, 27),
      endDate: DateTime(2026, 8, 3),
      status: TripStatus.active,
      category: 'Relaxation',
      days: 7,
      progress: 0.4,
      accommodation: const Accommodation(
        name: 'Astra Suites, Imerovigli',
        imageUrl: DummyImages.hotelRoom,
        description: 'Exceptional service with panoramic sunset views.',
      ),
      foodSpots: const [
        FoodSpot(
          name: 'Selene',
          imageUrl: DummyImages.seafood,
          description: 'Fine dining in a former monastery.',
        ),
        FoodSpot(
          name: 'Ammoudi Fish Tavern',
          imageUrl: DummyImages.taverna,
          description: 'Fresh catch by the Oia shoreline.',
        ),
      ],
      itinerary: const [
        ItineraryDay(
          dayNumber: 1,
          title: 'Arrival & Sunset Gazing',
          subtitle: 'Settling into the volcanic rhythm of the island.',
          activities: [
            ItineraryActivity(
              time: '09:00',
              title: 'Morning',
              description:
                  'Land at JTR Airport. Private transfer to Imerovigli. '
                  'Check-in at Astra Suites.',
              icon: 'flight_land',
            ),
            ItineraryActivity(
              time: '14:00',
              title: 'Afternoon',
              description:
                  'Leisurely walk from Imerovigli towards Fira along the '
                  'caldera edge. Stopped for iced coffee at a local cafe.',
              icon: 'hiking',
            ),
            ItineraryActivity(
              time: '19:30',
              title: 'Evening',
              description:
                  'Welcome dinner at Selene. Wine pairing featuring local '
                  'Assyrtiko grapes.',
              icon: 'wine_bar',
            ),
          ],
        ),
        ItineraryDay(
          dayNumber: 2,
          title: 'Sailing the Caldera',
          subtitle: 'Exploring the island from the crystalline waters.',
          activities: [
            ItineraryActivity(
              time: '10:00',
              title: 'Day Trip',
              description:
                  'Semi-private catamaran cruise. Visited the Red Beach and '
                  'White Beach for swimming and snorkeling.',
              icon: 'sailing',
              imageUrl: DummyImages.catamaran,
            ),
          ],
        ),
        ItineraryDay(
          dayNumber: 3,
          title: 'Ancient Thera & Oia',
          subtitle: 'History and the quintessential blue dome shots.',
          activities: [
            ItineraryActivity(
              time: '08:30',
              title: 'Breakfast at Astra Suites',
              description: 'A traditional Greek breakfast on the terrace.',
              icon: 'restaurant',
              imageUrl: DummyImages.breakfast,
              status: ActivityStatus.completed,
            ),
            ItineraryActivity(
              time: '11:00',
              title: 'Sailing the Caldera',
              description: 'Live now — semi-private catamaran cruise.',
              icon: 'sailing',
              status: ActivityStatus.active,
            ),
            ItineraryActivity(
              time: '19:30',
              title: 'Dinner at Ammoudi Fish Tavern',
              description: 'Fresh catch by the Oia shoreline.',
              icon: 'restaurant',
              status: ActivityStatus.upcoming,
              bookingReference: '#SNTR-88219',
            ),
          ],
        ),
      ],
      liveTimeline: const [
        ItineraryActivity(
          time: '08:30 AM',
          title: 'Breakfast at Astra Suites',
          description: 'A traditional Greek breakfast on the terrace.',
          icon: 'restaurant',
          imageUrl: DummyImages.breakfast,
          status: ActivityStatus.completed,
        ),
        ItineraryActivity(
          time: '11:00 AM – NOW',
          title: 'Sailing the Caldera',
          description: 'Semi-private catamaran cruise around the caldera.',
          icon: 'sailing',
          status: ActivityStatus.active,
        ),
        ItineraryActivity(
          time: '07:30 PM',
          title: 'Dinner at Ammoudi Fish Tavern',
          description: 'Fresh catch by the Oia shoreline.',
          icon: 'restaurant',
          status: ActivityStatus.upcoming,
          bookingReference: '#SNTR-88219',
        ),
      ],
    ),
    Trip(
      id: 'kyoto',
      destination: 'Kyoto Temples',
      country: 'Japan',
      coverImageUrl: DummyImages.kyoto,
      startDate: DateTime(2026, 5, 3),
      endDate: DateTime(2026, 5, 13),
      status: TripStatus.completed,
      category: 'Culture',
      days: 10,
      itinerary: const [
        ItineraryDay(
          dayNumber: 1,
          title: 'Tea Ceremonies & Zen Gardens',
          subtitle: 'A quiet introduction to old Kyoto.',
          activities: [
            ItineraryActivity(
              time: '09:00',
              title: 'Morning',
              description:
                  'Traditional tea ceremony in a machiya townhouse near '
                  'Gion.',
              icon: 'restaurant',
            ),
            ItineraryActivity(
              time: '15:00',
              title: 'Afternoon',
              description:
                  'Walked the red Torii gates at Fushimi Inari at golden '
                  'hour.',
              icon: 'hiking',
            ),
          ],
        ),
      ],
    ),
    Trip(
      id: 'northern-lights',
      destination: 'Northern Lights',
      country: 'Iceland',
      coverImageUrl: DummyImages.northernLights,
      startDate: DateTime(2026, 2, 4),
      endDate: DateTime(2026, 2, 9),
      status: TripStatus.completed,
      category: 'Adventure',
      days: 5,
      itinerary: const [
        ItineraryDay(
          dayNumber: 1,
          title: 'Reykjavik & Aurora Hunting',
          subtitle: 'Chasing green skies outside the city lights.',
          activities: [
            ItineraryActivity(
              time: '21:00',
              title: 'Evening',
              description:
                  'Guided aurora tour outside Reykjavik. Clear skies, '
                  'vivid green and purple bands.',
              icon: 'hiking',
            ),
          ],
        ),
      ],
    ),
  ];

  static const mapPins = <TripMapPin>[
    TripMapPin(
      tripId: 'santorini',
      label: 'Santorini',
      xPercent: 0.48,
      yPercent: 0.42,
    ),
    TripMapPin(tripId: 'kyoto', label: 'Kyoto', xPercent: 0.78, yPercent: 0.38),
    TripMapPin(
      tripId: 'northern-lights',
      label: 'Reykjavik',
      xPercent: 0.18,
      yPercent: 0.22,
    ),
  ];

  static const recommendations = <Recommendation>[
    Recommendation(
      destination: 'Amalfi Coast',
      country: 'Italy',
      imageUrl: DummyImages.amalfi,
      tagline:
          'Based on your love for Santorini, TIO recommends this '
          'stunning coastal escape.',
      isTopPick: true,
    ),
    Recommendation(
      destination: 'AlUla',
      country: 'Saudi Arabia',
      imageUrl: DummyImages.desert,
      tagline: 'Trending for Adventure',
    ),
    Recommendation(
      destination: 'Nuwara Eliya',
      country: 'Sri Lanka',
      imageUrl: DummyImages.teaFields,
      tagline: 'Eco-Nature',
    ),
    Recommendation(
      destination: 'CDMX',
      country: 'Mexico',
      imageUrl: DummyImages.cityStreet,
      tagline: 'Gastronomy & Art',
    ),
  ];

  static final socialFeed = <SocialPost>[
    SocialPost(
      id: 'post-1',
      authorName: 'Elena Vance',
      authorAvatarUrl: DummyImages.avatar1,
      postedAt: DateTime.now().subtract(const Duration(hours: 2)),
      location: 'Kyoto, Japan',
      type: SocialPostType.photo,
      imageUrl: DummyImages.torii,
      caption:
          "Just finished the ultimate AI-mapped cherry blossom route. The "
          "crowds were heavy, but TIO found a hidden shrine that was "
          "completely empty at sunrise! 🌸",
      likes: 1200,
      comments: 84,
    ),
    SocialPost(
      id: 'post-2',
      authorName: 'Marcus Chen',
      authorAvatarUrl: DummyImages.avatar2,
      postedAt: DateTime.now().subtract(const Duration(hours: 6)),
      location: 'Bali, Indonesia',
      type: SocialPostType.itinerary,
      itineraryTitle: 'Digital Nomad: North Bali Loop',
      itinerarySteps: const [
        ItineraryStep(
          title: 'Ubud Jungle Co-working',
          subtitle: 'High speed fiber & great coffee.',
        ),
        ItineraryStep(
          title: 'Gitgit Waterfall Hike',
          subtitle: 'TIO suggests 7AM for zero crowds.',
        ),
        ItineraryStep(
          title: 'Lovina Dolphin Watch',
          subtitle: 'Sunset session with the crew.',
        ),
      ],
      contributorAvatarUrls: const [
        DummyImages.avatar3,
        DummyImages.avatar4,
        DummyImages.avatar5,
      ],
    ),
  ];

  static const friends = <Friend>[
    Friend(
      id: 'f1',
      name: 'Jameson Blake',
      avatarUrl: DummyImages.avatar3,
      status: FriendStatus.activeNow,
    ),
    Friend(
      id: 'f2',
      name: 'Sarah Oh',
      avatarUrl: DummyImages.avatar4,
      status: FriendStatus.readyToTravel,
    ),
    Friend(
      id: 'f3',
      name: 'Oliver Kent',
      avatarUrl: DummyImages.avatar5,
      status: FriendStatus.away,
      statusDetail: 'Greece',
    ),
  ];

  static const profile = UserProfile(
    id: 'me',
    name: 'Alex Sterling',
    email: 'alex.explorer@tio.io',
    avatarUrl: DummyImages.me,
    title: 'Global Explorer',
    level: 24,
    travelPersonalities: ['Wild Nomad', 'Museum Maven'],
    travelRhythm: 'Spontaneous',
  );

  static const travelPersonalities = <TravelPersonality>[
    TravelPersonality(
      title: 'Museum Maven',
      description: 'You live for history, art galleries, and architectural '
          'wonders.',
      icon: 'museum',
    ),
    TravelPersonality(
      title: 'Rave Enthusiast',
      description: 'The night is young. You want festivals, clubs, and '
          'late-night street food.',
      icon: 'celebration',
    ),
    TravelPersonality(
      title: 'Luxury Seeker',
      description: '5-star comfort, private tours, and exclusive dining '
          'experiences.',
      icon: 'diamond',
    ),
    TravelPersonality(
      title: 'Wild Nomad',
      description: 'Off-the-beaten-path trails, camping, and pure '
          'adrenaline.',
      icon: 'hiking',
    ),
  ];

  static const travelRhythms = <String>[
    'Slow & Steady',
    'Action Packed',
    'Spontaneous',
    'Highly Structured',
  ];
}
