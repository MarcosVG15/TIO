import '../models/profile.dart';
import 'mock_data.dart';

abstract class ProfileRepository {
  Future<UserProfile> getProfile();
  Future<UserProfile> updateProfile(UserProfile profile);
}

/// Holds the profile in memory for the lifetime of the app session — edits
/// made on the Profile screen persist across navigation but reset on
/// restart, same as any other unsaved local cache would before a real
/// backend/database is attached.
class MockProfileRepository implements ProfileRepository {
  UserProfile _current = MockData.profile;

  @override
  Future<UserProfile> getProfile() async {
    await Future.delayed(const Duration(milliseconds: 400));
    return _current;
  }

  @override
  Future<UserProfile> updateProfile(UserProfile profile) async {
    await Future.delayed(const Duration(milliseconds: 500));
    _current = profile;
    return _current;
  }
}
