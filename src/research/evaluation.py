"""Repeatable heldout review evaluation. Labels are not place observations.

CLI reads an operator-provided, permitted dataset. Output contains no raw text,
review IDs or source hashes; a whole-file report hash identifies the input.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import resource
import time
from .language import LocalLanguageDetector,detector_metadata,evaluate_predictions
from .profiles import city_profile,QUALITY_POLICY_VERSION,PROFILE_VERSION


def evaluate_dataset(payload,*,detector=None):
    profile=city_profile(payload.get('city'))
    if profile['status']!='available':raise ValueError('CITY_LANGUAGE_PROFILE_UNAVAILABLE')
    development=payload.get('development',[]);heldout=payload.get('heldout',[])
    if not isinstance(development,list) or not isinstance(heldout,list) or len(development)+len(heldout)>20000:raise ValueError('DATASET_LIMIT')
    def text_key(row):
        if not isinstance(row,dict) or not isinstance(row.get('text'),str) or not 1<=len(row['text'])<=20000:raise ValueError('INVALID_EVALUATION_TEXT')
        return hashlib.sha256(row['text'].strip().encode()).hexdigest()
    dev_keys={text_key(r) for r in development};test_keys=[text_key(r) for r in heldout]
    duplicate_count=len(set(test_keys)&dev_keys)+len(test_keys)-len(set(test_keys))
    shared_places=len({r.get('place_id') for r in development if r.get('place_id')} & {r.get('place_id') for r in heldout if r.get('place_id')})
    detector=detector or LocalLanguageDetector();started=time.monotonic();rows=[]
    for row in heldout:
        expected=row.get('expected')
        if not isinstance(expected,str) or len(expected)>10:raise ValueError('MISSING_LANGUAGE_LABEL')
        value=detector.detect(row['text'])
        rows.append({'expected':expected,'predicted':value.get('language')})
    result=evaluate_predictions(rows,local_languages=profile['local_languages'],domain='independently_labeled_restaurant_reviews' if payload.get('domain')=='restaurant_reviews' else 'general_corpus')
    original_checks=sum(r.get('original_checked') is True for r in heldout)
    original_errors=sum(r.get('original_checked') is True and r.get('original_mismatch') is True for r in heldout)
    place_labels_complete=all(isinstance(row.get('place_id'),str) and row['place_id'] for row in [*development,*heldout])
    disjoint=not duplicate_count and not shared_places and place_labels_complete
    eligible=bool(result['production_strict_gate_supported'] and disjoint and original_checks>=20 and not original_errors and payload.get('synthetic') is False)
    result.update(production_strict_gate_supported=eligible,classification_quality='passed' if eligible else 'CLASSIFICATION_QUALITY_UNVERIFIED',
        city=payload['city'],language_profile_version=PROFILE_VERSION,quality_policy_version=QUALITY_POLICY_VERSION,
        original_checks=original_checks,original_translation_errors=original_errors,duplicate_text_count=duplicate_count,shared_place_count=shared_places,
        heldout_disjoint=disjoint,place_labels_complete=place_labels_complete,synthetic=payload.get('synthetic') is not False,
        model=detector_metadata(),detector_version=getattr(detector,'version','unknown'),
        processing_milliseconds=round((time.monotonic()-started)*1000,2),memory_peak_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        memory_units='bytes on macOS; KiB on Linux',provider_build=payload.get('provider_build'),category=payload.get('category'),
        source_population_inference=False,review_distribution_merged=False)
    return result


def main():
    parser=argparse.ArgumentParser(description='Offline heldout review-language evaluation; never enables product controls.')
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.input.stat().st_size>16*1024*1024:parser.error('Input exceeds 16MiB')
    raw=args.input.read_bytes();report=evaluate_dataset(json.loads(raw));report['input_sha256']=hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');args.output.chmod(0o600)
    print(json.dumps({'output':str(args.output),'passed':report['production_strict_gate_supported'],'labels':report['count']}))
if __name__=='__main__':main()
