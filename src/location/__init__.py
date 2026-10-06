"""Private, permission-gated location adapters. Production defaults to OFF."""
from .geometry import coordinate_pair, coordinates, haversine_straight_line
from .providers import (GeocodingProvider, RouteProvider, DisabledGeocodingProvider,
    DisabledRouteProvider, FakeGeocodingProvider, FakeRouteProvider, build_location_providers)
from .service import MatrixService
