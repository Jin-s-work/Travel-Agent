"""Rule/evaluation contracts. Synthetic strings are not accuracy evidence."""
from types import SimpleNamespace
import pytest

from src.research.language import LocalLanguageDetector, detector_metadata, base_language, evaluate_predictions


def score(language,value):
    return SimpleNamespace(language=SimpleNamespace(iso_code_639_1=SimpleNamespace(name=language.upper())),value=value)


class Engine:
    def __init__(self,values=None,segments=()):
        self.values=values or [score('en',.9),score('es',.05)]
        self.segments=segments
    def compute_language_confidence_values(self,text): return self.values
    def detect_multiple_languages_of(self,text): return self.segments


@pytest.mark.parametrize('text',[None,'','  ','😀🍣👍','12345','寿司ラーメン','Sushi ramen','Pa amb tomàquet'])
def test_insufficient_or_menu_text_is_unknown_without_loading_model(text):
    detector=LocalLanguageDetector(engine_factory=lambda:pytest.fail('Do not load model for insufficient text'))
    result=detector.detect(text)
    assert result['language'] is None
    assert result['model_confidence'] is None


def test_bcp47_original_tag_preserved_and_disagreement_abstains():
    detector=LocalLanguageDetector(engine_factory=lambda:Engine([score('es',.9),score('ca',.05)]))
    result=detector.detect('Este restaurante tiene comida muy deliciosa y buenos precios.',provider_language='es-419')
    assert result['language']=='es' and result['original_language_tag']=='es-419'
    result=detector.detect('Este restaurante tiene comida muy deliciosa y buenos precios.',provider_language='ko')
    assert result['language'] is None and result['disagreement_reason']=='PROVIDER_MODEL_DISAGREEMENT'
    assert base_language('ja-JP')=='ja' and base_language('not_a_locale') is None


def test_low_relative_score_and_margin_are_not_confident_languages():
    for values in ([score('es',.69),score('ca',.05)],[score('es',.75),score('ca',.60)]):
        detector=LocalLanguageDetector(engine_factory=lambda:Engine(values))
        result=detector.detect('The original text contains enough words for an attempted classification.')
        assert result['language'] is None and result['disagreement_reason']=='LOW_CONFIDENCE'


@pytest.mark.parametrize('text',[
    '料理はとても美味しかったです。 음식이 정말 맛있었어요.',
    '음식과 서비스가 아주 좋았습니다. The service was attentive and everything tasted great.',
])
def test_substantial_mixed_scripts_are_unknown_before_model(text):
    detector=LocalLanguageDetector(engine_factory=lambda:pytest.fail('Mixed input must abstain'))
    assert detector.detect(text)['disagreement_reason']=='MIXED_SCRIPTS'


def test_meaningful_latin_language_mixture_is_unknown():
    first='The service was attentive and very pleasant. '
    second='El menjar era molt bo i el servei perfecte.'
    class Mixed(Engine):
        def compute_language_confidence_values(self,text):
            return [score('ca',.95),score('en',.01)] if text==second else [score('en',.95),score('ca',.01)]
    engine=Mixed(segments=[SimpleNamespace(start_index=0,end_index=len(first)),SimpleNamespace(start_index=len(first),end_index=len(first+second))])
    result=LocalLanguageDetector(engine_factory=lambda:engine).detect(first+second)
    assert result['language'] is None and result['disagreement_reason']=='MIXED_LANGUAGES'


@pytest.mark.parametrize('exception,reason',[(ModuleNotFoundError,'MODEL_UNAVAILABLE'),(RuntimeError,'MODEL_FAILURE')])
def test_model_failures_never_default_to_a_language(exception,reason):
    def missing(): raise exception('sensitive raw content should never be returned')
    result=LocalLanguageDetector(engine_factory=missing).detect('This is sufficiently long original text for language classification.')
    assert result['language'] is None and result['disagreement_reason']==reason
    assert 'sensitive' not in str(result)


def test_oversize_text_not_silently_truncated_and_classified():
    detector=LocalLanguageDetector(engine_factory=lambda:pytest.fail('Must not classify oversized input'))
    assert detector.detect('a'*20001)['disagreement_reason']=='TEXT_TOO_LONG'


def test_zero_korean_support_is_null_not_pass():
    rows=[{'expected':'ja','predicted':'ja'} for _ in range(100)]
    result=evaluate_predictions(rows,domain='independently_labeled_restaurant_reviews')
    assert result['korean_recall'] is None
    assert result['korean_support']==0 and not result['support_sufficient']
    assert not result['production_strict_gate_supported']


def test_unknown_korean_is_false_negative_and_languages_have_denominators():
    rows=[{'expected':'ko','predicted':'ko'},{'expected':'ko','predicted':None},
          {'expected':'ja','predicted':'ko'},{'expected':'es','predicted':'ca'}]
    result=evaluate_predictions(rows,local_languages=('es','ca'))
    assert result['korean_tp']==1 and result['korean_fp']==1 and result['korean_fn']==1
    assert result['korean_recall']==.5 and result['korean_precision']==.5
    assert result['confusion_matrix']['ko']['unknown']==1
    assert result['local_precision']==1.0
    assert result['per_language']['es']['fn']==1 and result['per_language']['ca']['fp']==1


def test_general_text_even_perfect_never_enables_review_gate():
    rows=[{'expected':language,'predicted':language} for language in ('ja','ko','es','ca','en') for _ in range(30)]
    result=evaluate_predictions(rows,local_languages=('es','ca'),domain='FLORES-200 general text')
    assert result['numerical_target_passed']
    assert not result['production_strict_gate_supported']
    assert result['classification_quality']=='CLASSIFICATION_QUALITY_UNVERIFIED'


def test_development_data_never_enables_review_gate():
    rows=[{'expected':language,'predicted':language} for language in ('ja','ko') for _ in range(60)]
    result=evaluate_predictions(rows,domain='independently_labeled_restaurant_reviews',split='development')
    assert result['numerical_target_passed'] and not result['production_strict_gate_supported']


def test_actual_local_model_and_artifact_metadata():
    pytest.importorskip('lingua')
    info=detector_metadata()
    assert info['available'] and len(info['model_sha256'])==64
    assert info['license']=='Apache-2.0' and info['model_bytes']>0
    detector=LocalLanguageDetector()
    japanese=detector.detect('この店の料理はとても美味しかったので、また家族と一緒に来たいと思います。')
    korean=detector.detect('음식이 아주 맛있었고 직원분들이 친절하게 설명해 주셔서 가족들과 다시 방문하고 싶습니다.')
    assert japanese['language']=='ja' and korean['language']=='ko'
    assert japanese['classification_quality_verified'] is False
