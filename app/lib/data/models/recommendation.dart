/// An AI-suggested destination shown on Home's "Potential New Destinations"
/// bento grid — stands in for a future recommendation engine call.
class Recommendation {
  const Recommendation({
    required this.destination,
    required this.country,
    required this.imageUrl,
    required this.tagline,
    this.isTopPick = false,
    this.description,
  });

  final String destination;
  final String country;
  final String imageUrl;
  final String tagline;
  final bool isTopPick;
  final String? description;
}
