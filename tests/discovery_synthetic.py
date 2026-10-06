"""Explicit synthetic candidate packs. Production never imports this module."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone


def pack(city='tokyo'):
    now = datetime.now(timezone.utc)
    stamp, expires = now.isoformat(), (now + timedelta(days=60)).isoformat()
    zone = 'Asia/Tokyo' if city == 'tokyo' else 'Europe/Madrid'
    currency = 'JPY' if city == 'tokyo' else 'EUR'
    coordinate = (35.68, 139.77) if city == 'tokyo' else (41.385, 2.174)
    places = []
    for n in range(1, 7):
        category = 'restaurant' if n <= 4 else ('cafe' if n == 5 else 'attraction')
        title = f'합성 {"東京" if city == "tokyo" else "Barcelona"} {n} · 非常に長い名前の地域食堂と文化散歩'
        sources = [{'key': key, 'url': f'https://example.org/{city}/{n}/{key}',
            'source_type': 'synthetic', 'source_group': key, 'checked_at': stamp,
            'read_confirmed': True, 'display_permitted': True,
            'evidence_note': 'Synthetic test evidence only; not an actual recommendation.'}
            for key in ('official', 'regional_a', 'regional_b')]
        def fact(field, value, **extra):
            return {'field': field, 'value': value, 'status': 'verified', 'source_key': 'official',
                    'checked_at': stamp, 'expires_at': expires, **extra}
        facts = [
            fact('local_evidence', {'source_groups': ['regional_a', 'regional_b'], 'direct_confirmation': False}),
            fact('iconic_evidence', {'source_groups': ['regional_a', 'regional_b'], 'level': 'city'}),
            fact('opening_hours', {'timezone': zone, 'weekly': {str(d): [['10:00', '22:00']] for d in range(7)}}, valid_for_date='2026-11-06'),
            fact('max_party', 6 if n != 3 else 2),
            fact('min_party', 1),
            fact('children_rule', {'allowed': True, 'minimum_age': 0}),
            fact('closed', n == 4),
            fact('price', {'currency': currency, 'amount_min': '1000' if city == 'tokyo' else '10.00',
                'amount_max': '2000' if city == 'tokyo' else '20.00', 'basis': 'per_person',
                'period': 'meal' if category != 'attraction' else 'visit', 'tax': 'included', 'deposit': None}),
            fact('reservation_methods', ['official_website']),
            fact('reservation_url', f'https://example.org/{city}/{n}/booking'),
            fact('facility_capacity', 25),
            fact('dietary', {'vegetarian': True, 'nut_free': False}),
            fact('accessibility', {'step_free': True}),
            fact('rating', {'platform': 'synthetic', 'scale': 5, 'rating': 4.5,
                'total_rating_count': 300, 'comparison_cohort': city + '_test', 'usage_permitted': True}),
        ]
        places.append({'external_id': f'synthetic-{city}-{n}', 'name': title,
            'native_name': title, 'address': f'SYNTHETIC ONLY {n} {city}', 'category': category,
            'canonical_url': f'https://example.org/{city}/{n}', 'chain_id': 'synthetic_chain' if n <= 2 else None,
            'neighborhood': 'synthetic_neighborhood', 'tags': ['culture', 'noodles'] if n <= 4 else ['art'],
            'recommendation_types': ['local_discovery', 'landmark'],
            'latitude': coordinate[0] + n * .001, 'longitude': coordinate[1] + n * .001,
            'sources': sources, 'facts': facts})
    return {'version': 'synthetic-discovery-v1-' + city, 'city': city, 'synthetic': True, 'places': deepcopy(places)}


def conditions(city='tokyo', adults=4):
    return {'city': city, 'visit': {'date': '2026-11-06', 'local_time': '12:00',
        'timezone': 'Asia/Tokyo' if city == 'tokyo' else 'Europe/Madrid'},
        'party': {'adults': adults, 'children': []}, 'categories': ['restaurant'],
        'recommendation_types': ['local_discovery', 'landmark'],
        'origin': {'label': '합성 출발점', 'latitude': 35.68 if city == 'tokyo' else 41.385,
                   'longitude': 139.77 if city == 'tokyo' else 2.174},
        'budget': {'currency': 'JPY' if city == 'tokyo' else 'EUR',
            'amount_max': '3000' if city == 'tokyo' else '30.00', 'basis': 'per_person', 'period': 'meal'},
        'density': 'balanced', 'required': {'dietary': [], 'accessibility': []},
        'preferred': {'tags': ['noodles'], 'dietary': []}}
