"""Reviewed language sets are separate from city input support and launch gates."""
from src.destinations import CITIES

PROFILE_VERSION='city-languages-v1'
QUALITY_POLICY_VERSION='review-language-quality-v2'
QUALITY_POLICY={'min_labels':100,'min_original_checks':20,'min_korean_truth':50,
                'min_local_truth':50,'min_local_predictions':50,'local_precision':.95,'korean_recall':.95}
PROFILES={
 'tokyo':{'languages':['ja'],'source':'https://www.gotokyo.org/book/wp-content/uploads/2026/03/AO2_2603_tg_low_JP.pdf'},
 'barcelona':{'languages':['es','ca'],'source':'https://www.barcelona.cat/internationalwelcome/sites/default/files/10-tips_EN.pdf'},
}

def city_profile(city):
    profile=PROFILES.get(city)
    # Korean-city support must not silently change the disjoint L/K definition.
    overlap=city in {'seoul','busan','jeju'} or bool(profile and 'ko' in profile['languages'])
    return {'city':city,'input_supported':city in CITIES,'version':PROFILE_VERSION,
            'local_languages':profile['languages'] if profile else [],
            'source':profile['source'] if profile else None,'checked_at':'2026-10-07' if profile else None,
            'status':'available' if profile and not overlap else 'unavailable',
            'reason_codes':['OVERLAPPING_LANGUAGE_SETS'] if overlap else [] if profile else ['CITY_LANGUAGE_PROFILE_UNREVIEWED']}

def quality_support(body):
    local=body['local']; korean=body['korean']
    support={'city_labels':body['label_count'],'original_checks':body['original_checks'],
             'korean_truth':korean['tp']+korean['fn'],'local_truth':local['tp']+local['fn'],
             'local_predictions':local['tp']+local['fp']}
    sufficient=(support['city_labels']>=100 and support['original_checks']>=20 and
                min(support[k] for k in ('korean_truth','local_truth','local_predictions'))>=50)
    return support,sufficient
