from src.discovery.photos import unavailable
import pytest

@pytest.mark.parametrize('reason,state',[
    ('NO_LINKED_PHOTO','no_photo'),('PHOTO_PROVIDER_UNAVAILABLE','provider_failed'),
    ('PHOTO_POLICY_EXPIRED','expired'),('PHOTO_IDENTITY_MISMATCH','identity_unresolved'),
    ('PHOTO_METADATA_INVALID','policy_blocked'),('PHOTO_DISABLED','policy_blocked')])
def test_photo_absence_transport_permission_and_identity_are_distinct(reason,state):
    value=unavailable(reason)
    assert value['photo_status']['availability_state']==state
    assert value['photos']==[]
