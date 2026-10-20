"""Browserless part of the stage 1 E2E: real OIDC round trip and E02 owner isolation over HTTP."""
from tests.e2e.conftest import oidc_login, requires_e2e

pytestmark = requires_e2e


def test_synthetic_oidc_login_for_both_users(stage1_server):
    for who in ('a', 'b'):
        client = oidc_login(stage1_server, who)
        trips = client.get('/api/v2/trips').json()['items']
        assert trips and all(trip['title'].startswith(who.upper() + ' · ') for trip in trips)


def test_e02_user_b_gets_404_for_every_user_a_trip_url(stage1_server):
    a, b = oidc_login(stage1_server, 'a'), oidc_login(stage1_server, 'b')
    a_trips = a.get('/api/v2/trips').json()['items']
    titles = {trip['title'] for trip in a_trips}
    paths = []
    for trip in a_trips:
        base = '/api/v2/trips/' + trip['id']
        bookings = a.get(base + '/bookings').json()['items']
        paths += [base, base + '/bookings', base + '/bookmarks', base + '/discovery-conditions',
                  base + '/itineraries', base + '/accommodations', base + '/documents', base + '/jobs']
        paths += [base + '/bookings/' + booking['id'] for booking in bookings]
    for path in paths:
        assert a.get(path).status_code == 200, path
        denied = b.get(path)
        assert denied.status_code == 404, (path, denied.status_code)
        assert not any(title in denied.text for title in titles), path
    assert not {trip['title'] for trip in b.get('/api/v2/trips').json()['items']} & titles
