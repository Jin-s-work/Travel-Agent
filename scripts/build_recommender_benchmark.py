"""Build an explicitly synthetic development rubric; never a real accuracy set.

The grades below are authored scenario expectations, not ranker outputs. The
model must not be tuned and advertised as validated on these same scenarios.
Run from the repository: python -m scripts.build_recommender_benchmark
"""
from copy import deepcopy
import json
from pathlib import Path
from tests.test_recommendation_engine import NOW, catalog, snapshot, fact
from tests.test_stage2_general_models import valid_local


def build():
    queries = []
    for city in ('tokyo', 'barcelona'):
        for scenario in ('taste', 'origin', 'rating', 'local_taste', 'cold_start', 'closed'):
            places = []
            for i in range(5):
                row = valid_local(catalog(city)[0])
                row.update(place_id=f'{city}_{scenario}_{i}', name=f'합성 장소 {i}',
                           native_name=f'SYNTHETIC {i}', tags=['ramen'], chain_id=None, neighborhood=f'area_{i}')
                row['facts'] = [f for f in row['facts'] if f['field'] != 'tags']
                places.append(row)
            value = snapshot(city, movement_version='v2', evaluation_at=NOW.isoformat(), limit=3)
            value['conditions'].update(origin=None, preferred={'tags': []}, budget=None)
            section = 'local_discovery' if scenario == 'local_taste' else 'landmark'
            grades = [2, 2, 2, 2, 2]
            if scenario in ('taste', 'local_taste'):
                value['conditions']['preferred']['tags'] = ['sushi']
                places[3]['tags'] = ['sushi']; places[4]['tags'] = ['sushi', 'ramen']
                grades = [0, 0, 0, 3, 2]
            elif scenario == 'origin':
                lat, lon = places[0]['latitude'], places[0]['longitude']
                value['conditions']['origin'] = {'label': 'Synthetic hotel', 'latitude': lat, 'longitude': lon}
                for place, offset in zip(places, [.08, .06, .04, .001, .01]):
                    place['latitude'] = lat + offset
                grades = [0, 0, 1, 3, 2]
            elif scenario == 'rating':
                for place, rating, count in zip(places, [4.2, 4.3, 4.6, 4.9, 5], [10000, 3000, 600, 600, 1]):
                    fact(place, 'iconic_evidence')['value'] = {'evidence_type': 'platform_popular', 'platform': 'synthetic'}
                    fact(place, 'rating')['value'].update(rating=rating, total_rating_count=count, platform='synthetic')
                grades = [0, 1, 2, 3, 1]
            elif scenario == 'closed':
                fact(places[0], 'closed')['value'] = True
                fact(places[1], 'max_party')['value'] = 1
                grades = [0, 0, 2, 2, 2]
            queries.append({'id': f'{city}_{scenario}', 'split': 'development', 'section': section,
                'rubric': scenario, 'snapshot': value, 'candidates': places,
                'judgments': {p['place_id']: grade for p, grade in zip(places, grades)}})
    return {'schema_version': 1, 'label_source': 'synthetic_rubric', 'k': 3,
            'limitations': 'Engineering-authored development scenarios. No independent holdout or user outcomes.',
            'queries': queries}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    path = root / 'docs/service-v4/fixtures/hybrid-v4-development.json'
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + '\n')
    print(f'Wrote {path.name}: synthetic development only, no provider calls')
