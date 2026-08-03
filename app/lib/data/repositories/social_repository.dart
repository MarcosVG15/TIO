import '../models/social.dart';
import 'mock_data.dart';

abstract class SocialRepository {
  Future<List<SocialPost>> getFeed();
  Future<List<Friend>> getFriends();
}

class MockSocialRepository implements SocialRepository {
  @override
  Future<List<SocialPost>> getFeed() async {
    await Future.delayed(const Duration(milliseconds: 500));
    return MockData.socialFeed;
  }

  @override
  Future<List<Friend>> getFriends() async {
    await Future.delayed(const Duration(milliseconds: 350));
    return MockData.friends;
  }
}
