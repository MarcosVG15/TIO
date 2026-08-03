import '../models/profile.dart';
import 'mock_data.dart';

/// Sign-in and onboarding submission. Every method here is a placeholder for
/// a real call (Firebase Auth, Supabase, a custom REST endpoint, ...) — the
/// UI only depends on this interface, so wiring up real auth later is a
/// matter of writing one new implementation class.
abstract class AuthRepository {
  Future<UserProfile> signInWithGoogle();
  Future<UserProfile> signInWithApple();
  Future<UserProfile> continueAsReturningUser();
  Future<UserProfile> startNewJourney();
  Future<void> submitOnboarding({
    required List<String> travelPersonalities,
    required String? travelRhythm,
  });
}

class MockAuthRepository implements AuthRepository {
  @override
  Future<UserProfile> signInWithGoogle() => _fakeSignIn();

  @override
  Future<UserProfile> signInWithApple() => _fakeSignIn();

  @override
  Future<UserProfile> continueAsReturningUser() => _fakeSignIn();

  @override
  Future<UserProfile> startNewJourney() => _fakeSignIn();

  Future<UserProfile> _fakeSignIn() async {
    await Future.delayed(const Duration(milliseconds: 700));
    return MockData.profile;
  }

  @override
  Future<void> submitOnboarding({
    required List<String> travelPersonalities,
    required String? travelRhythm,
  }) async {
    await Future.delayed(const Duration(milliseconds: 600));
  }
}
