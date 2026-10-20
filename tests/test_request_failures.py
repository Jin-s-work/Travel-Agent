import json
import pytest
from src.operations.failures import diagnostic
from tests.test_foundation_api import service

@pytest.mark.parametrize('code',['55P03','57014','53300','40001','40P01'])
def test_busy_is_classified_without_logging_private_exception_text(code):
    error=RuntimeError('private email SQL token question'); error.sqlstate=code
    result=diagnostic(error)
    assert result['busy']
    assert 'private' not in json.dumps(result)
    assert result['errors'][0]['sqlstate']==code

def test_mixed_or_unexpected_error_is_not_claimed_retryable():
    locked=RuntimeError('secret');locked.sqlstate='55P03'
    assert not diagnostic(ExceptionGroup('sensitive',[locked,ValueError('private')]))['busy']
    assert not diagnostic(ValueError('private'))['busy']
    assert 'sensitive' not in json.dumps(diagnostic(ExceptionGroup('sensitive',[locked])))

@pytest.mark.parametrize('busy',[True,False])
def test_http_error_has_safe_recovery_contract(service,monkeypatch,caplog,busy):
    from src.operations import controls
    client=service.login('failure-contract').client
    def failed(_con):
        error=RuntimeError('PRIVATE_QUERY_AND_TOKEN')
        if busy:error.sqlstate='55P03'
        raise error
    monkeypatch.setattr(controls,'read',failed)
    response=client.get('/api/v2/trips')
    assert response.status_code==(503 if busy else 500)
    assert response.json()['error']['code']==('DATABASE_BUSY' if busy else 'INTERNAL_ERROR')
    assert response.json()['error']['retryable'] is busy
    assert 'PRIVATE_QUERY_AND_TOKEN' not in response.text+caplog.text
    assert 'RuntimeError' in caplog.text
    if busy:assert response.headers['Retry-After']=='3'
