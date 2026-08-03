import '../models/map_pin.dart';
import '../models/trip.dart';
import 'mock_data.dart';

/// Everything the UI needs about a user's trips.
///
/// This is the seam where a real backend plugs in later: implement this
/// interface against your REST/GraphQL API or local database, then swap the
/// override in `lib/data/providers/providers.dart` — no screen changes
/// needed.
abstract class TripRepository {
  Future<List<Trip>> getPastTrips();
  Future<Trip?> getActiveTrip();
  Future<Trip?> getTripById(String id);
  Future<List<TripMapPin>> getMapPins();
}

/// In-memory implementation backed by [MockData], standing in for a future
/// API/database call. The artificial delay mirrors real network latency so
/// loading states are exercised even in this UI-only build.
class MockTripRepository implements TripRepository {
  @override
  Future<List<Trip>> getPastTrips() async {
    await Future.delayed(const Duration(milliseconds: 500));
    return MockData.trips
        .where((t) => t.status == TripStatus.completed)
        .toList();
  }

  @override
  Future<Trip?> getActiveTrip() async {
    await Future.delayed(const Duration(milliseconds: 400));
    for (final trip in MockData.trips) {
      if (trip.status == TripStatus.active) return trip;
    }
    return null;
  }

  @override
  Future<Trip?> getTripById(String id) async {
    await Future.delayed(const Duration(milliseconds: 400));
    for (final trip in MockData.trips) {
      if (trip.id == id) return trip;
    }
    return null;
  }

  @override
  Future<List<TripMapPin>> getMapPins() async {
    await Future.delayed(const Duration(milliseconds: 400));
    return MockData.mapPins;
  }
}
