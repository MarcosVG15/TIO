import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/map_pin.dart';
import '../models/profile.dart';
import '../models/recommendation.dart';
import '../models/social.dart';
import '../models/trip.dart';
import '../repositories/auth_repository.dart';
import '../repositories/profile_repository.dart';
import '../repositories/recommendation_repository.dart';
import '../repositories/social_repository.dart';
import '../repositories/trip_repository.dart';

// --- Repositories --------------------------------------------------------
//
// Every repository is exposed through a `Provider` returning the mock
// implementation. To connect a real backend later, override these in a
// `ProviderScope(overrides: [...])` (or swap the constructor call directly)
// with a repository that talks to your API/database — every provider below
// keeps working unchanged because screens only ever depend on the abstract
// repository interfaces and the providers that expose them.

final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => MockAuthRepository(),
);

final tripRepositoryProvider = Provider<TripRepository>(
  (ref) => MockTripRepository(),
);

final recommendationRepositoryProvider = Provider<RecommendationRepository>(
  (ref) => MockRecommendationRepository(),
);

final socialRepositoryProvider = Provider<SocialRepository>(
  (ref) => MockSocialRepository(),
);

final profileRepositoryProvider = Provider<ProfileRepository>(
  (ref) => MockProfileRepository(),
);

// --- Trips -----------------------------------------------------------------

final pastTripsProvider = FutureProvider<List<Trip>>(
  (ref) => ref.watch(tripRepositoryProvider).getPastTrips(),
);

final activeTripProvider = FutureProvider<Trip?>(
  (ref) => ref.watch(tripRepositoryProvider).getActiveTrip(),
);

final tripByIdProvider = FutureProvider.family<Trip?, String>(
  (ref, id) => ref.watch(tripRepositoryProvider).getTripById(id),
);

final mapPinsProvider = FutureProvider<List<TripMapPin>>(
  (ref) => ref.watch(tripRepositoryProvider).getMapPins(),
);

final recommendationsProvider = FutureProvider<List<Recommendation>>(
  (ref) => ref.watch(recommendationRepositoryProvider).getRecommendations(),
);

// --- Social ------------------------------------------------------------

final socialFeedProvider = FutureProvider<List<SocialPost>>(
  (ref) => ref.watch(socialRepositoryProvider).getFeed(),
);

final friendsProvider = FutureProvider<List<Friend>>(
  (ref) => ref.watch(socialRepositoryProvider).getFriends(),
);

// --- Profile -------------------------------------------------------------

/// Mutable profile state — screens call `ref.read(profileControllerProvider
/// .notifier).save(...)` to persist edits through [ProfileRepository].
class ProfileController extends AsyncNotifier<UserProfile> {
  @override
  Future<UserProfile> build() {
    return ref.watch(profileRepositoryProvider).getProfile();
  }

  Future<void> save(UserProfile updated) async {
    state = const AsyncLoading<UserProfile>().copyWithPrevious(state);
    state = await AsyncValue.guard(
      () => ref.read(profileRepositoryProvider).updateProfile(updated),
    );
  }

  Future<void> togglePushNotifications(bool enabled) async {
    final current = state.valueOrNull;
    if (current == null) return;
    await save(current.copyWith(pushNotificationsEnabled: enabled));
  }
}

final profileControllerProvider =
    AsyncNotifierProvider<ProfileController, UserProfile>(
      ProfileController.new,
    );
