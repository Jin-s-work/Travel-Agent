#!/usr/bin/env python3
"""Explicit offline-model evaluation; downloads occur only with --fetch-corpus.

The official FLORES-200 general-text corpus is a diagnostic, NOT independent
restaurant-review evidence. Its scores cannot enable a production strict gate.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import sys
import tarfile
import time
import unicodedata
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.research.language import LocalLanguageDetector, detector_metadata, evaluate_predictions

URL='https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz'
LANGUAGES={'ja':'jpn_Jpan','ko':'kor_Hang','es':'spa_Latn','ca':'cat_Latn','en':'eng_Latn'}
LICENSE='https://creativecommons.org/licenses/by-sa/4.0/'


def sha(data): return hashlib.sha256(data).hexdigest()


def prepare_corpus(directory, *, fetch=False):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    manifest_path=directory/'manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        for item in manifest['files']:
            path=directory/item['path']
            if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file() or sha(path.read_bytes())!=item['sha256']:
                raise ValueError('Corpus checksum mismatch; do not evaluate modified labels/text')
        return manifest
    if not fetch:
        raise ValueError('Corpus absent. Run explicitly with --fetch-corpus; startup never downloads data.')
    archive=directory/'flores200_dataset.tar.gz'
    if not archive.exists():
        request=urllib.request.Request(URL,headers={'User-Agent':'Travel-Agent-language-evaluation/1'})
        with urllib.request.urlopen(request,timeout=30) as response, archive.with_suffix('.tmp').open('wb') as output:
            size=0
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>40*1024*1024: raise ValueError('Corpus download exceeded fixed size cap')
                output.write(block)
        archive.with_suffix('.tmp').replace(archive)
    files=[]
    with tarfile.open(archive,'r:gz') as tar:
        members={member.name:member for member in tar.getmembers()}
        for split in ('dev','devtest'):
            for language,code in LANGUAGES.items():
                name=f'./flores200_dataset/{split}/{code}.{split}'
                member=members[name]
                if not member.isfile() or member.size>2*1024*1024: raise ValueError('Unexpected corpus member')
                data=tar.extractfile(member).read()
                target=directory/f'{split}.{language}.txt'
                target.write_bytes(data)
                files.append({'path':target.name,'archive_member':name,'split':split,'language':language,
                              'bytes':len(data),'sha256':sha(data),'rows':len(data.decode('utf-8').splitlines())})
    manifest={'dataset':'FLORES-200','source_url':URL,'source_documentation':'https://github.com/facebookresearch/flores/blob/main/flores200/README.md',
        'fetched_at':datetime.now(timezone.utc).isoformat(),'archive_sha256':sha(archive.read_bytes()),
        'license':'CC-BY-SA-4.0','license_url':LICENSE,'attribution':'NLLB Team (2022), No Language Left Behind: Scaling Human-Centered Machine Translation',
        'modifications':'Only five language files selected; original sentence text unchanged. Evaluation sampling is deterministic.',
        'domain':'professionally translated general web article sentences; NOT restaurant reviews','files':files}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (directory/'ATTRIBUTION.txt').write_text(manifest['attribution']+'\n'+manifest['source_documentation']+'\nCC BY-SA 4.0: '+LICENSE+'\n'+manifest['modifications']+'\n')
    return manifest


def text_hash(text): return sha(' '.join(unicodedata.normalize('NFC',text).casefold().split()).encode())


def evaluate(directory, manifest, *, dev_per_language=50, heldout_per_language=200):
    detector=LocalLanguageDetector()
    info=detector_metadata()
    if not info['available']: raise RuntimeError('Install the pinned lingua wheel first; no automatic model download')
    rows={}; seen_dev=set(); skipped=Counter(); started=time.monotonic()
    for split,limit in (('dev',dev_per_language),('devtest',heldout_per_language)):
        rows[split]=[]
        for language in LANGUAGES:
            values=(directory/f'{split}.{language}.txt').read_text().splitlines()
            seen_split=set(); chosen=0
            for index,text in enumerate(values):
                fingerprint=text_hash(text)
                if fingerprint in seen_split or (split=='devtest' and fingerprint in seen_dev):
                    skipped[split]+=1; continue
                seen_split.add(fingerprint)
                if split=='dev': seen_dev.add(fingerprint)
                prediction=detector.detect(text)
                rows[split].append({'id':f'{split}:{LANGUAGES[language]}:{index+1}',
                    'expected':language,'predicted':prediction['language'],'text_sha256':fingerprint,
                    'reason':prediction['disagreement_reason'],'confidence':prediction['model_confidence']})
                chosen+=1
                if chosen>=limit: break
    # The same source sentence can appear in several languages. Counts are
    # language examples, not independent travelers, reviews, or city samples.
    dev_hashes={row['text_sha256'] for row in rows['dev']}
    heldout_hashes={row['text_sha256'] for row in rows['devtest']}
    if dev_hashes & heldout_hashes: raise AssertionError('Tuning/heldout overlap')
    metrics={}
    for split in rows:
        label='development' if split=='dev' else 'heldout'
        metrics[label]={city:evaluate_predictions(rows[split],local_languages=languages,domain='FLORES-200 general text',split=label)
            for city,languages in {'tokyo_profile':('ja',),'barcelona_profile':('es','ca')}.items()}
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform!='darwin': peak*=1024
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'detector':info,
        'platform':platform.platform(),'python':platform.python_version(),'peak_rss_bytes':peak,
        'runtime_seconds':time.monotonic()-started,'corpus':manifest,
        'evaluation_design':{'dev_per_language':dev_per_language,'heldout_per_language':heldout_per_language,
            'sampling':'first unique sentences in official split file; fixed before scoring',
            'dev_heldout_text_hash_overlap':0,'skipped_duplicates':dict(skipped),'threshold_tuning':'none; rules fixed before corpus evaluation',
            'training_corpus_overlap':'not audited; independence from detector pretraining not claimed',
            'actual_city_review_samples':{'Tokyo':0,'Barcelona':0}},
        'metrics':metrics,'errors':{split:[row for row in samples if row['expected']!=row['predicted']] for split,samples in rows.items()},
        'evaluated_ids':{split:[row['id'] for row in samples] for split,samples in rows.items()},
        'production_strict_gate_supported':False,'reason':'CLASSIFICATION_QUALITY_UNVERIFIED: no independent restaurant-review labels'}


def markdown(report):
    info=report['detector']; heldout=report['metrics']['heldout']
    def percentage(value): return 'null' if value is None else f'{100*value:.2f}%'
    lines=['# 로컬 언어 판별 실행 결과','',
        '**운영 엄격 언어 필터는 미검증 상태로 유지한다.** 실제 모델로 공개 일반 문장 진단을 실행했지만, 도쿄·바르셀로나 음식점 리뷰의 독립 라벨 평가가 아니다. 실제 도시별 리뷰 평가 건수는 각각 0건이다.','',
        f"실행 시각: {report['generated_at']}. Python {report['python']}, {report['platform']}.",
        f"모델: `{info['detector_version']}`. 설치 binary(모델 포함) SHA256 `{info['model_sha256']}`. 크기 {info['model_bytes']:,} bytes. 모델 패키지 라이선스 Apache-2.0.",
        f"프로세스 peak RSS: {report['peak_rss_bytes']/1024**2:.2f} MiB. 분류 실행 {report['runtime_seconds']:.3f}초. RSS는 Python/모델/평가 코드 전체 최고값이며 서버 전체 메모리나 여러 worker 합산값이 아니다.",
        '', '[Lingua 공식 문서](https://github.com/pemistahl/lingua-py)는 offline API와 mixed-language 기능을 설명한다. 혼합 판별을 확정 진실로 취급하지 않고 짧은 문구·낮은 점수·의미 있는 혼합은 unknown으로 보존한다. confidence는 정답 확률이나 현지인 확률이 아니다.',
        '', '## 데이터와 분리', '',
        '[FLORES-200 공식 안내](https://github.com/facebookresearch/flores/blob/main/flores200/README.md)의 공개 일반 문장을 사용했다. 원 배포자는 NLLB Team(2022)이며 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 조건을 따른다. 이 자료는 식당 리뷰가 아니며 지역·방문자 분포를 나타내지 않는다.',
        '', f"공식 dev에서 언어별 {report['evaluation_design']['dev_per_language']}건, 별도 devtest에서 언어별 {report['evaluation_design']['heldout_per_language']}건(ja/ko/es/ca/en)을 선택했다. 개발·heldout 정규화 본문 해시 겹침은 0건이다. 이 실행에서 기준 조정은 하지 않았다. 모델 학습 데이터와의 겹침은 조사하지 않았으므로 학습 독립성도 주장하지 않는다.",
        '', '## Heldout 진단', '',
        '| 현지어 프로필 | 전체 문장 | 현지어 precision | 한국어 recall | 한국어 precision | 한국어 TP/FP/FN | unknown | 운영 필터 |',
        '| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |']
    for city,row in heldout.items():
        lines.append(f"| {city} | {row['count']} | {percentage(row['local_precision'])} | {percentage(row['korean_recall'])} | {percentage(row['korean_precision'])} | {row['korean_tp']}/{row['korean_fp']}/{row['korean_fn']} | {row['unknown_count']} ({percentage(row['unknown_ratio'])}) | 미검증 |")
    row=heldout['tokyo_profile']
    lines+=['','| 정답 언어 | 정답 지원 수 | TP | FP | FN(unknown 포함) | unknown | precision | recall |','| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for language,item in row['per_language'].items():
        lines.append(f"| {language} | {item['support']} | {item['tp']} | {item['fp']} | {item['fn']} | {item['unknown']} | {percentage(item['precision'])} | {percentage(item['recall'])} |")
    lines+=['','혼동 행렬(정답 → 판정, unknown 포함):','', '```json',json.dumps(row['confusion_matrix'],ensure_ascii=False,indent=2),'```','',
        '한국어 정답이 없는 별도 평가 입력은 recall=null, support_sufficient=false로 계산한다. unknown도 한국어 false negative 분모에 포함한다. 합성 fixture는 규칙·분모·예외 처리 테스트이며 언어 정확도 증거로 집계하지 않는다.',
        '', '## 재현과 남은 검증', '', '```bash',
        '.venv/bin/python scripts/review_language_eval.py --fetch-corpus',
        '# 이후 캐시와 체크섬을 재사용하는 오프라인 실행',
        '.venv/bin/python scripts/review_language_eval.py',
        'OPENAI_API_KEY=test .venv/bin/python -m pytest tests/test_review_language.py -q','```','',
        '다운로드는 위 명시적 CLI 옵션으로만 수행한다. 서버 시작/판별 함수는 모델이나 자료를 다운로드하지 않는다. 파일별 원본 URL·크기·SHA256, 전체 평가 ID와 오류 ID는 LANGUAGE_EVALUATION.json에 있다. 원문은 Git 제외 `data/review-language-eval`에 보존하며 외부 API로 전송하지 않는다.',
        '', '다음 승인 조건은 도시별 최소 100개 이상의 독립 음식점 리뷰 라벨, 실제 한국어 지원 사례, 현지어 precision/한국어 recall 각각 95% 목표와 언어별 오류·unknown 검토다. 이 보고서의 일반문장 결과만으로 classification_evaluation을 passed로 설정하면 안 된다.']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-corpus',action='store_true')
    parser.add_argument('--corpus-dir',type=Path,default=ROOT/'data/review-language-eval')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'docs/service-v2/reports')
    parser.add_argument('--dev-per-language',type=int,default=50)
    parser.add_argument('--heldout-per-language',type=int,default=200)
    args=parser.parse_args()
    if not 1<=args.dev_per_language<=997 or not 1<=args.heldout_per_language<=1012:
        parser.error('sample sizes exceed corpus split bounds')
    manifest=prepare_corpus(args.corpus_dir,fetch=args.fetch_corpus)
    report=evaluate(args.corpus_dir,manifest,dev_per_language=args.dev_per_language,heldout_per_language=args.heldout_per_language)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'LANGUAGE_EVALUATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (args.output_dir/'LANGUAGE_EVALUATION.md').write_text(markdown(report))
    print(json.dumps({'evaluated':sum(len(x) for x in report['evaluated_ids'].values()),'peak_rss_bytes':report['peak_rss_bytes'],
        'runtime_seconds':report['runtime_seconds'],'heldout_profiles':{key:{name:value[name] for name in ('local_precision','korean_recall','unknown_count')} for key,value in report['metrics']['heldout'].items()},
        'production_strict_gate_supported':False}))


if __name__=='__main__': main()
