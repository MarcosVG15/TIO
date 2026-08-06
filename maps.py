"""Map data for a planned trip.

Nothing in the deployed frontend can render tiles - there is no Leaflet,
MapLibre or Mapbox in the bundle - so this does not draw a map. It serves
everything a map needs, in the two forms that are useful without asking
permission from the frontend build:

  * GeoJSON, which every map library and QGIS reads directly. Point per stop,
    LineString per day in visiting order.
  * A directions deep link per day, which works today with no map component at
    all: it opens Google Maps with the day's stops as waypoints.

The route is the plan's own order, not a re-optimised one. The composer already
sequences a day geographically, and quietly reordering it would mean the map
disagreed with the itinerary the traveller is reading. Instead the total walking
distance is reported, so a day that zigzags is visible rather than hidden.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import quote

#: Morning before afternoon before evening. Anything unrecognised sorts last,
#: which is where an unlabelled stop belongs.
_PART_ORDER = {"morning": 0, "afternoon": 1, "evening": 2}

_EARTH_KM = 6371.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometres between two (lat, lon) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(h))


def _sort_key(stop: dict) -> tuple:
    return (
        stop.get("day") or 0,
        _PART_ORDER.get((stop.get("part_of_day") or "").lower(), 9),
        stop.get("order") or 0,
    )


def centroid(stops: Sequence[dict]) -> Optional[tuple[float, float]]:
    """Where a map should open. None when nothing has coordinates.

    The mean of the stops rather than the first one: opening on the first stop
    puts half the trip off-screen when a day spreads out.
    """
    points = [
        (s["latitude"], s["longitude"])
        for s in stops
        if s.get("latitude") is not None and s.get("longitude") is not None
    ]
    if not points:
        return None
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def bounds(stops: Sequence[dict]) -> Optional[list[float]]:
    """[west, south, east, north] - what a map should zoom to fit."""
    points = [
        (s["latitude"], s["longitude"])
        for s in stops
        if s.get("latitude") is not None and s.get("longitude") is not None
    ]
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return [min(lons), min(lats), max(lons), max(lats)]


def directions_url(stops: Sequence[dict]) -> Optional[str]:
    """Google Maps walking directions through a day's stops.

    Works with no map component at all, which is why it is here: a link the
    traveller can open on their phone is worth more today than a map view that
    needs a frontend release.
    """
    points = [
        f"{s['latitude']},{s['longitude']}"
        for s in stops
        if s.get("latitude") is not None and s.get("longitude") is not None
    ]
    if len(points) < 2:
        return None
    origin, destination, waypoints = points[0], points[-1], points[1:-1]
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote(origin)}&destination={quote(destination)}"
        "&travelmode=walking"
    )
    if waypoints:
        url += "&waypoints=" + quote("|".join(waypoints))
    return url


def day_distance_km(stops: Sequence[dict]) -> float:
    """How far the day's route actually walks, in order."""
    located = [
        (s["latitude"], s["longitude"])
        for s in stops
        if s.get("latitude") is not None and s.get("longitude") is not None
    ]
    return round(
        sum(haversine_km(located[i], located[i + 1]) for i in range(len(located) - 1)), 2
    )


def geojson(stops: Iterable[dict], trip_name: str = "") -> dict[str, Any]:
    """A FeatureCollection any map library can render unmodified.

    One Point per stop carrying enough properties to label and colour it, and
    one LineString per day in visiting order.
    """
    ordered = sorted(stops, key=_sort_key)
    features: list[dict[str, Any]] = []

    for stop in ordered:
        lat, lon = stop.get("latitude"), stop.get("longitude")
        if lat is None or lon is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "itinerary_id": stop.get("itinerary_id"),
                    "name": stop.get("name"),
                    "title": stop.get("title"),
                    "day": stop.get("day"),
                    "part_of_day": stop.get("part_of_day"),
                    "category": stop.get("category"),
                    "completed": bool(stop.get("completed")),
                },
            }
        )

    by_day: dict[int, list[dict]] = {}
    for stop in ordered:
        if stop.get("latitude") is None or stop.get("longitude") is None:
            continue
        by_day.setdefault(stop.get("day") or 1, []).append(stop)

    for day, day_stops in sorted(by_day.items()):
        if len(day_stops) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [s["longitude"], s["latitude"]] for s in day_stops
                    ],
                },
                "properties": {
                    "day": day,
                    "stops": len(day_stops),
                    "distance_km": day_distance_km(day_stops),
                    "directions_url": directions_url(day_stops),
                },
            }
        )

    located = [s for s in ordered
               if s.get("latitude") is not None and s.get("longitude") is not None]
    middle = centroid(located)

    return {
        "type": "FeatureCollection",
        "features": features,
        # Not part of the GeoJSON spec, but every consumer wants them and
        # recomputing a centroid client-side from the features is busywork.
        "properties": {
            "trip": trip_name,
            "center": {"latitude": middle[0], "longitude": middle[1]} if middle else None,
            "bounds": bounds(located),
            "days": len(by_day),
            "located": len(located),
            "unlocated": len(ordered) - len(located),
        },
    }
