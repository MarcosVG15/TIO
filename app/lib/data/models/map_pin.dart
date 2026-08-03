/// A visited destination shown as a pin on the travel map, positioned with
/// simple relative coordinates (percentage of the map canvas) rather than
/// real lat/lng — good enough for a static illustrative map, and trivial to
/// replace once real geo-coordinates + a map SDK are wired up.
class TripMapPin {
  const TripMapPin({
    required this.tripId,
    required this.label,
    required this.xPercent,
    required this.yPercent,
  });

  final String tripId;
  final String label;
  final double xPercent;
  final double yPercent;
}
