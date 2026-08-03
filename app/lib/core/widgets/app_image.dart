import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

/// A network image with rounded corners and a graceful placeholder/error
/// fallback — used everywhere a destination photo or avatar is shown.
///
/// All imagery today comes from placeholder URLs (see [DummyImages]); once a
/// backend exists this widget keeps working unchanged, only the URLs change.
class AppImage extends StatelessWidget {
  const AppImage(
    this.url, {
    super.key,
    this.fit = BoxFit.cover,
    this.borderRadius,
  });

  final String url;
  final BoxFit fit;
  final BorderRadius? borderRadius;

  @override
  Widget build(BuildContext context) {
    final radius = borderRadius ?? BorderRadius.zero;
    final scheme = Theme.of(context).colorScheme;

    return ClipRRect(
      borderRadius: radius,
      child: CachedNetworkImage(
        imageUrl: url,
        fit: fit,
        placeholder: (context, _) => Container(
          color: scheme.surfaceContainerHigh,
        ),
        errorWidget: (context, _, _) => Container(
          color: scheme.surfaceContainerHigh,
          alignment: Alignment.center,
          child: Icon(
            Icons.image_outlined,
            color: scheme.onSurfaceVariant.withValues(alpha: 0.5),
          ),
        ),
      ),
    );
  }
}

/// Curated set of stable placeholder photo URLs, grouped by theme, so
/// screens can reference something evocative of the mockups (Santorini,
/// Kyoto, mountains, food, portraits...) without depending on a backend.
class DummyImages {
  DummyImages._();

  static const santorini = 'https://picsum.photos/id/1058/900/600';
  static const kyoto = 'https://picsum.photos/id/1015/900/600';
  static const northernLights = 'https://picsum.photos/id/1036/900/600';
  static const amalfi = 'https://picsum.photos/id/1044/900/600';
  static const desert = 'https://picsum.photos/id/1043/900/600';
  static const teaFields = 'https://picsum.photos/id/1039/900/600';
  static const cityStreet = 'https://picsum.photos/id/1031/900/600';
  static const mountainPass = 'https://picsum.photos/id/1018/900/600';
  static const tuscany = 'https://picsum.photos/id/1019/600/400';
  static const museum = 'https://picsum.photos/id/1053/600/400';
  static const nightCity = 'https://picsum.photos/id/1074/600/400';
  static const hotelRoom = 'https://picsum.photos/id/1048/600/400';
  static const seafood = 'https://picsum.photos/id/292/200/200';
  static const taverna = 'https://picsum.photos/id/312/200/200';
  static const catamaran = 'https://picsum.photos/id/1054/700/400';
  static const breakfast = 'https://picsum.photos/id/292/200/200';
  static const torii = 'https://picsum.photos/id/1041/900/600';
  static const bali = 'https://picsum.photos/id/1040/600/400';

  static const avatar1 = 'https://i.pravatar.cc/150?img=12';
  static const avatar2 = 'https://i.pravatar.cc/150?img=32';
  static const avatar3 = 'https://i.pravatar.cc/150?img=45';
  static const avatar4 = 'https://i.pravatar.cc/150?img=5';
  static const avatar5 = 'https://i.pravatar.cc/150?img=48';
  static const me = 'https://i.pravatar.cc/300?img=68';
}
