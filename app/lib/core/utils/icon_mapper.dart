import 'package:flutter/material.dart';

/// Maps the small, closed vocabulary of icon keys used by mock/API data
/// (e.g. "sailing", "restaurant") to a Flutter [IconData]. Keeping icon
/// identity as a string in the data layer means a future backend can send
/// the same keys without coupling API payloads to Flutter types.
IconData iconForKey(String key) {
  switch (key) {
    case 'flight_land':
      return Icons.flight_land;
    case 'flight_takeoff':
      return Icons.flight_takeoff;
    case 'hiking':
      return Icons.hiking;
    case 'wine_bar':
      return Icons.wine_bar;
    case 'sailing':
      return Icons.sailing;
    case 'restaurant':
      return Icons.restaurant;
    case 'bed':
      return Icons.bed;
    case 'museum':
      return Icons.museum;
    case 'celebration':
      return Icons.celebration;
    case 'diamond':
      return Icons.diamond_outlined;
    case 'check_circle':
      return Icons.check_circle;
    case 'photo_camera':
      return Icons.photo_camera;
    case 'add_a_photo':
      return Icons.add_a_photo;
    case 'confirmation_number':
      return Icons.confirmation_number;
    case 'star':
      return Icons.star;
    default:
      return Icons.circle;
  }
}
