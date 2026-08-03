# TIO — Flutter App

UI implementation of the TIO travel companion, matching the "Terra —
Rooted Warmth" design system in [`../Frontend/terra/DESIGN.md`](../Frontend/terra/DESIGN.md)
and the screen mockups in `../Frontend/`.

This is currently a **UI-only build**: all data comes from in-memory mock
repositories. It's structured so wiring up a real backend/database later is a
small, contained change — not a rewrite.

## Stack

- **Flutter 3.44 / Dart 3.12**
- **flutter_riverpod** — state management & dependency injection
- **go_router** — declarative routing, incl. the bottom-nav tab shell
- **google_fonts** — Literata (headlines) + Nunito Sans (body), fetched at
  runtime
- **cached_network_image** — placeholder photos (via picsum.photos /
  i.pravatar.cc) with graceful offline fallback

## Structure

```
lib/
  core/            theme, router, shared widgets, icon-key mapping
  data/
    models/        plain Dart data classes (Trip, UserProfile, SocialPost, ...)
    repositories/   abstract repository interfaces + Mock* implementations
    providers/      Riverpod providers wiring repositories to the UI
  features/
    auth/           Welcome / sign-in screen
    onboarding/      "Getting to know you" travel-soul questionnaire
    home/           Dashboard: past trips, AI recommendations, chat preview
    trips/          Map, trip details (itinerary), live "Active Trip Explorer"
    social/         Feed + friends
    profile/        Account settings, preferences, recalibration
    shell/          Bottom-nav shell wrapping the 4 main tabs
```

## Connecting a real backend

Every screen depends only on an abstract repository interface
(`TripRepository`, `SocialRepository`, `ProfileRepository`,
`RecommendationRepository`, `AuthRepository` in `lib/data/repositories/`) —
never directly on the `Mock*` classes or on `MockData`. To go live:

1. Write a new class implementing the relevant interface (e.g.
   `ApiTripRepository`, `SupabaseTripRepository`) that calls your real
   API/database instead of returning `MockData`.
2. Swap the constructor in `lib/data/providers/providers.dart`, e.g.:

   ```dart
   final tripRepositoryProvider = Provider<TripRepository>(
     (ref) => ApiTripRepository(ref.watch(apiClientProvider)),
   );
   ```

No screen or widget code needs to change — they all consume the
`FutureProvider`/`AsyncNotifier` providers built on top of these
repositories, and already render real loading/error states via
`AsyncValueView` (`lib/core/widgets/async_value_view.dart`).

`lib/data/repositories/mock_data.dart` is the single place holding today's
placeholder content; it disappears once every repository has a real
implementation.

## Running

```
flutter pub get
flutter run            # pick a connected device/emulator
flutter run -d chrome   # web
```
