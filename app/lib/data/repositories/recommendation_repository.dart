import '../models/recommendation.dart';
import 'mock_data.dart';

/// AI-suggested destinations for Home's "Potential New Destinations" grid.
/// Stands in for a future recommendation-engine endpoint.
abstract class RecommendationRepository {
  Future<List<Recommendation>> getRecommendations();
}

class MockRecommendationRepository implements RecommendationRepository {
  @override
  Future<List<Recommendation>> getRecommendations() async {
    await Future.delayed(const Duration(milliseconds: 500));
    return MockData.recommendations;
  }
}
