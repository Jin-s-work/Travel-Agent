"""Offline language signals; abstention is preferable to a fabricated language.

This module never downloads models, calls an API, or infers residence/nationality.
The model's relative score is not a calibrated probability of being correct.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from importlib import metadata
import hashlib
from pathlib import Path
import re
import unicodedata

RULES_VERSION = 'review-language-rules-v1'
MIN_LETTERS = 8
MIN_LATIN_WORDS = 4
MIN_CONFIDENCE = 0.70
MIN_MARGIN = 0.20
MAX_CHARACTERS = 20000


def base_language(tag):
    if not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*',tag.strip()):
        return None
    base=tag.strip().split('-',1)[0].lower()
    return {'jpn':'ja','kor':'ko','spa':'es','cat':'ca','eng':'en'}.get(base,base)


@lru_cache(maxsize=1)
def _engine():
    # A missing wheel is an unavailable model, never an installation trigger.
    from lingua import LanguageDetectorBuilder
    return LanguageDetectorBuilder.from_all_languages().build()


@lru_cache(maxsize=1)
def detector_metadata():
    try:
        package=metadata.distribution('lingua-language-detector')
    except metadata.PackageNotFoundError:
        return {'available':False,'package_version':None,'detector_version':f'lingua-unavailable/{RULES_VERSION}',
                'license':'Apache-2.0','model_sha256':None,'model_bytes':None}
    paths=[Path(package.locate_file(file)) for file in package.files or [] if str(file).endswith(('.so','.pyd'))]
    digest=hashlib.sha256(); size=0
    try:
        for path in sorted(paths):
            # Models are bundled into the native artifact; hash the actual installed
            # package binary, not a marketing version string or model download URL.
            with path.open('rb') as source:
                for block in iter(lambda:source.read(1024*1024),b''):
                    digest.update(block); size+=len(block)
    except OSError:
        paths=[]
    return {'available':bool(paths),'package_version':package.version,
            'detector_version':f'lingua-{package.version}/{RULES_VERSION}',
            'license':'Apache-2.0','model_sha256':digest.hexdigest() if paths else None,
            'model_bytes':size if paths else None,'hash_scope':'installed native binary with bundled models',
            'language_scope':'all 75 Lingua languages','network_required':False,
            'confidence_semantics':'relative model score; not calibrated correctness or residence probability',
            'rules':{'min_letters':MIN_LETTERS,'min_latin_words':MIN_LATIN_WORDS,
                     'min_confidence':MIN_CONFIDENCE,'min_margin':MIN_MARGIN,'max_characters':MAX_CHARACTERS}}


def _script_counts(text):
    counts=Counter()
    for char in text:
        if not char.isalpha(): continue
        name=unicodedata.name(char,'')
        if 'HANGUL' in name: counts['hangul']+=1
        elif 'HIRAGANA' in name or 'KATAKANA' in name or 'CJK' in name: counts['japanese_or_han']+=1
        elif 'LATIN' in name: counts['latin']+=1
        else: counts['other']+=1
    return counts


class LocalLanguageDetector:
    @property
    def version(self):
        """Stable rule/model identity for durable collection checkpoints."""
        return detector_metadata()['detector_version']

    def __init__(self, *, engine_factory=None):
        self.engine_factory=engine_factory or _engine

    def detect(self, text, provider_language=None):
        info=detector_metadata()
        result={'language':None,'detector_version':info['detector_version'],'model_confidence':None,
                'disagreement_reason':None,'text_status':'unknown','original_language_tag':provider_language,
                'classification_quality_verified':False}
        def unknown(reason,score=None):
            result.update(disagreement_reason=reason,model_confidence=score)
            return result
        if not isinstance(text,str): return unknown('NO_ORIGINAL_TEXT')
        text=unicodedata.normalize('NFC',text).strip()
        if not text: return unknown('NO_ORIGINAL_TEXT')
        if len(text)>MAX_CHARACTERS: return unknown('TEXT_TOO_LONG')
        scripts=_script_counts(text)
        if sum(scripts.values())<MIN_LETTERS: return unknown('INSUFFICIENT_TEXT')
        if scripts['latin']==sum(scripts.values()) and len(re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?",text))<MIN_LATIN_WORDS:
            return unknown('SHORT_MENU_OR_PHRASE')
        if scripts['hangul']>=4 and scripts['japanese_or_han']>=4:
            return unknown('MIXED_SCRIPTS')
        # A substantial Latin sentence mixed with East Asian text is unknown;
        # short brand names alone do not trigger this rule.
        if scripts['latin']>=16 and max(scripts['hangul'],scripts['japanese_or_han'])>=8:
            return unknown('MIXED_SCRIPTS')
        try:
            engine=self.engine_factory()
            values=engine.compute_language_confidence_values(text)
            if not values: return unknown('MODEL_ABSTAINED')
            top=values[0]; score=float(top.value)
            language=top.language.iso_code_639_1.name.lower()
            runner_up=float(values[1].value) if len(values)>1 else 0.0
            if score<MIN_CONFIDENCE or score-runner_up<MIN_MARGIN:
                return unknown('LOW_CONFIDENCE',score)
            meaningful=set()
            for segment in engine.detect_multiple_languages_of(text):
                fragment=text[segment.start_index:segment.end_index]
                if sum(char.isalpha() for char in fragment)<12 or len(fragment.split())<3:
                    continue
                segment_values=engine.compute_language_confidence_values(fragment)
                if segment_values and float(segment_values[0].value)>=MIN_CONFIDENCE:
                    meaningful.add(segment_values[0].language.iso_code_639_1.name.lower())
            if len(meaningful)>1: return unknown('MIXED_LANGUAGES',score)
        except (ImportError,ModuleNotFoundError):
            return unknown('MODEL_UNAVAILABLE')
        except Exception:
            # Never turn failures into English/Japanese, or leak source text in errors.
            return unknown('MODEL_FAILURE')
        original=base_language(provider_language)
        if provider_language is not None and original is None:
            return unknown('INVALID_PROVIDER_LANGUAGE',score)
        if original and original!=language:
            return unknown('PROVIDER_MODEL_DISAGREEMENT',score)
        result.update(language=language,model_confidence=score,text_status='classified')
        return result

    __call__ = detect


def evaluate_predictions(rows, *, local_languages=('ja',), domain='unverified', split='heldout'):
    """Explicit denominators, false negatives and abstentions for labeled rows.

    General text diagnostics can never authorize a restaurant review gate.
    Empty Korean support means recall=null and support_sufficient=false.
    """
    rows=list(rows)
    local=set(local_languages)
    matrix=Counter((row['expected'],row.get('predicted') or 'unknown') for row in rows)
    labels=sorted({row['expected'] for row in rows}|{row.get('predicted') for row in rows if row.get('predicted')})
    per_language={}
    for language in labels:
        tp=sum(row['expected']==language and row.get('predicted')==language for row in rows)
        fp=sum(row['expected']!=language and row.get('predicted')==language for row in rows)
        fn=sum(row['expected']==language and row.get('predicted')!=language for row in rows)
        unknown=sum(row['expected']==language and not row.get('predicted') for row in rows)
        per_language[language]={'tp':tp,'fp':fp,'fn':fn,'support':tp+fn,'unknown':unknown,
            'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}
    ltp=sum(row['expected'] in local and row.get('predicted') in local for row in rows)
    lfp=sum(row['expected'] not in local and row.get('predicted') in local for row in rows)
    korean=per_language.get('ko',{'tp':0,'fp':0,'fn':0,'support':0,'unknown':0,'precision':None,'recall':None})
    local_precision=ltp/(ltp+lfp) if ltp+lfp else None
    support_sufficient=len(rows)>=100 and korean['support']>0 and ltp+lfp>0
    numerical_pass=bool(support_sufficient and local_precision>=.95 and korean['recall']>=.95)
    review_valid=domain=='independently_labeled_restaurant_reviews' and split=='heldout'
    return {'count':len(rows),'unknown_count':sum(not row.get('predicted') for row in rows),
        'unknown_ratio':sum(not row.get('predicted') for row in rows)/len(rows) if rows else None,
        'local_languages':sorted(local),'local_precision':local_precision,'local_tp':ltp,'local_fp':lfp,
        'korean_precision':korean['precision'],'korean_recall':korean['recall'],
        'korean_tp':korean['tp'],'korean_fp':korean['fp'],'korean_fn':korean['fn'],
        'korean_support':korean['support'],'support_sufficient':support_sufficient,
        'per_language':per_language,'confusion_matrix':{a:{b:n for (x,b),n in sorted(matrix.items()) if x==a} for a in labels},
        'domain':domain,'split':split,'numerical_target_passed':numerical_pass,
        'production_strict_gate_supported':numerical_pass and review_valid,
        'classification_quality':'passed' if numerical_pass and review_valid else 'CLASSIFICATION_QUALITY_UNVERIFIED'}
