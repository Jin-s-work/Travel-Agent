from datetime import datetime,timezone
from copy import deepcopy
import pytest
from tests.test_foundation_api import service,_trip,_upload
from src.travel_tools.rules import calculate,release_timing
from src.travel_tools.exports import calendar,inquiry

RULE={'type':'rolling_days','days_before_visit':30,'timezone':'Asia/Tokyo'}

def test_rules_dates_months_and_dst():
    assert calculate(RULE,'2027-03-31','Asia/Tokyo')['due_date']=='2027-03-01'
    assert calculate({'type':'monthly_release','target_month_offset':1,'release_day':31,'timezone':'Asia/Tokyo'},'2027-03-31','Asia/Tokyo')['due_precision']=='unknown'
    assert calculate(RULE,'2027-03-31','Asia/Tokyo')['due_at'] is None
    for day,reason in [('2026-03-29','NONEXISTENT_LOCAL_TIME'),('2026-10-25','AMBIGUOUS_LOCAL_TIME')]:
        value=calculate({'type':'fixed_datetime','explicit_local_date':day,'explicit_local_time':'02:30','timezone':'Europe/Madrid'},'2026-11-01','Europe/Madrid')
        assert value['reason']==reason and value['due_at'] is None
    assert calculate({**RULE,'explicit_local_time':'10:00'},'2026-11-06','Asia/Tokyo')['due_at'].startswith('2026-10-07T01:00')
    now=datetime(2026,10,6,16,tzinfo=timezone.utc)  # Already Oct 7 in Tokyo.
    assert release_timing(calculate(RULE,'2026-11-06','Asia/Tokyo'),'Asia/Tokyo',now)=='due_today'
    assert release_timing(calculate(RULE,'2026-11-05','Asia/Tokyo'),'Asia/Tokyo',now)=='past'
    assert release_timing(calculate({**RULE,'explicit_local_time':'10:00'},'2026-11-06','Asia/Tokyo'),'Asia/Tokyo',now)=='future'

def test_calendar_unicode_folding_injection_and_template():
    task=dict(id='task_test',version=2,title='東京;서울\nBEGIN:BAD'*30,updated_at='2026-10-01T00:00:00+00:00',timezone='Asia/Tokyo',status='waiting_open',calculation=calculate(RULE,'2026-11-06','Asia/Tokyo'),visit_date='2026-11-06',requested_time=None,party={'adults':4,'children':[{'age':None}]},requests='窓の席')
    value=calendar(task)
    assert all(len(line)<=75 for line in value.split(b'\r\n'))
    assert b'\r\nBEGIN:BAD' not in value
    assert b'DTSTART;VALUE=DATE:20261007' in value and b'DTEND' not in value
    assert b'UID:task_test@travel-agent.invalid' in value and b'SEQUENCE:2' in value
    task['rule']={'source':{'url':'https://example.com/reserve?token=private#secret'}}
    task['calculation']=calculate({**RULE,'explicit_local_time':'10:00'},task['visit_date'],task['timezone'])
    expanded=calendar(task).replace(b'\r\n ',b'').decode()
    assert '2026-10-07T10:00:00+09:00' in expanded and 'https://example.com/reserve' in expanded
    assert 'private' not in expanded and '#secret' not in expanded
    task['status']='cancelled';task['version']=3
    cancelled=calendar(task)
    assert b'STATUS:CANCELLED' in cancelled and b'SEQUENCE:3' in cancelled
    assert b'UID:task_test@travel-agent.invalid' in cancelled
    for lang in ('ja','es','ca'):
        draft=inquiry(task,lang)
        assert draft['slots']['requested_time'] is None and draft['external_action']=='none'
        assert '2026-11-06' in draft['text'] and '2026-11-06' in draft['korean']

def create(user,trip,**changes):
    return user.client.post(f"/api/v2/trips/{trip['id']}/reservation-tasks",json={'title':'예약 문의','visit_date':'2026-11-06','timezone':'Asia/Tokyo',**changes},headers={'Idempotency-Key':'intent-1'})

def test_task_transitions_scope_idempotency_version_and_persistence(service):
    a=service.login('A');b=service.login('B');trip=_trip(a.client);t2=_trip(a.client);tb=_trip(b.client)
    r=create(a,trip);assert r.status_code==201,r.text
    task=r.json();path=f"/api/v2/trips/{trip['id']}/reservation-tasks/{task['id']}"
    assert create(a,trip).json()['id']==task['id']
    assert create(a,trip,title='different').status_code==409
    assert b.client.get(path).status_code==404
    assert a.client.get(path.replace(trip['id'],t2['id'])).status_code==404
    assert a.client.get(path+'/calendar.ics').status_code==422
    r=a.client.patch(path,json={'expected_version':task['version'],'action':'report_complete'});assert r.status_code==200,r.text
    assert r.json()['status']=='user_completed' and r.json()['evidence'] is None
    assert a.client.patch(path,json={'expected_version':1,'action':'start'}).status_code==409
    assert a.client.get(path).json()['status']=='user_completed'
    draft=a.client.post(path+'/inquiry-drafts',json={'expected_version':2,'language':'es'})
    assert draft.status_code==200,draft.text
    assert a.client.get(path).json()['version']==2
    service.settings.preparation_enabled=False
    assert a.client.get(path).status_code==404

from tests.test_discovery_foundation import discovery,import_pack
from tests.discovery_synthetic import pack as synthetic_pack

def test_official_rule_state_evidence_match_and_source_revocation(discovery):
    data=synthetic_pack();place=data['places'][0];source=place['sources'][0]
    place['facts'].append({'field':'booking_open_rule','value':{**RULE,'explicit_local_time':'10:00'},'status':'verified','source_key':source['key'],'checked_at':source['checked_at'],'expires_at':place['facts'][0]['expires_at']})
    stored=import_pack(discovery,data);trip=_trip(discovery.client);pid=stored['places'][0]['place_id'];base=f"/api/v2/trips/{trip['id']}"
    detail=discovery.client.get(base+'/places/'+pid+'/detail').json();fact=next(f for f in detail['facts'] if f['field']=='booking_open_rule')
    user=discovery.admin
    response=create(user,trip,place_id=pid,rule_fact_id=fact['id']);assert response.status_code==201,response.text
    task=response.json();path=base+'/reservation-tasks/'+task['id'];assert task['status']=='waiting_open'
    first=user.client.get(path+'/calendar.ics').content;assert b'DTSTART:20261007T010000Z' in first
    assert first==user.client.get(path+'/calendar.ics').content
    done=user.client.patch(path,json={'action':'report_complete','expected_version':1}).json()
    discovery.state.parsed=[{'kind':'restaurant','provider':'Proof','date':'2026-11-06','place_id':pid,'party':{'adults':3,'children':[]},'status':'source_verified','events':[]}]
    _upload(user.client,trip['id'])
    # Document activation bumps trip version, so the task requires renewed review.
    task=user.client.get(path).json();assert task['status']=='needs_confirmation'
    task=user.client.patch(path,json={'action':'report_complete','expected_version':task['version']}).json()
    booking=user.client.get(base+'/bookings').json()['items'][0]
    bad=user.client.patch(path,json={'action':'verify_evidence','expected_version':task['version'],'booking_id':booking['id']})
    assert bad.status_code==422 and bad.json()['error']['code']=='EVIDENCE_MISMATCH',bad.text
    corrected=user.client.patch(base+'/bookings/'+booking['id'],json={'expected_version':booking['version'],'changes':[{'field_path':'party','value':task['party']}]});assert corrected.status_code==200,corrected.text
    task=user.client.get(path).json()
    if task['status']!='user_completed':task=user.client.patch(path,json={'action':'report_complete','expected_version':task['version']}).json()
    valid=user.client.patch(path,json={'action':'verify_evidence','expected_version':task['version'],'booking_id':booking['id']})
    assert valid.status_code==200,valid.text
    assert valid.json()['status']=='evidence_verified' and valid.json()['evidence']['method']=='user_reviewed_structured_match'
    source=next(s for s in detail['sources'] if s['id']==fact['source_id'])
    revoke=user.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={'expected_version':source['version'],'status':'revoked','read_confirmed':True,'display_permitted':False,'policy_version':source['policy_version'],'evidence':'Synthetic revocation'})
    assert revoke.status_code==200,revoke.text
    latest=user.client.get(path).json();assert latest['status']=='needs_confirmation'
    assert latest['calculation']['due_precision']=='unknown'
    assert user.client.get(path+'/calendar.ics').status_code==422

@pytest.mark.parametrize('visit,rule,expected',[
 ('2028-03-01',{**RULE,'days_before_visit':1},'2028-02-29'),
 ('2027-03-01',{**RULE,'days_before_visit':1},'2027-02-28'),
 ('2026-11-30',{'type':'monthly_release','target_month_offset':1,'release_day':1,'timezone':'Asia/Tokyo'},'2026-10-01'),
 ('2026-11-06',{'type':'fixed_datetime','explicit_local_date':'2026-10-10','timezone':'Asia/Tokyo'},'2026-10-10')])
def test_boundary_rule_contract(visit,rule,expected):
    result=calculate(rule,visit,'Asia/Tokyo');assert result['due_date']==expected and result['due_at'] is None


def test_inquiry_rejects_changed_dates_people_and_confirmation_claims():
    from src.travel_tools.exports import validate_draft
    task=dict(title='Restaurant',place_name='食堂',visit_date='2026-11-06',requested_time='18:30',timezone='Asia/Tokyo',party={'adults':2,'children':[{'age':8}]},requests='',request_keys=['nut_allergy'])
    original=inquiry(task,'ja')
    for key,wrong in [('visit_date','2026-11-07'),('requested_time','19:30'),('party',{'adults':5,'children':[]})]:
        candidate=deepcopy(original);candidate['slots'][key]=wrong
        result=validate_draft(task,candidate,'ja');assert result['template_fallback'] and result['slots']==original['slots']
    candidate=deepcopy(original);candidate['text']='予約は確定しました。'
    assert validate_draft(task,candidate,'ja')['template_fallback']
    assert 'ナッツアレルギー' in original['text'] and '견과류 알레르기' in original['korean']


def test_logout_has_api_response_and_revokes_session(service):
    user=service.login('logout')
    response=user.client.post('/api/v2/auth/logout',json={},follow_redirects=False)
    assert response.status_code==204 and 'location' not in response.headers
    assert user.client.get('/api/v2/session').json()['authenticated'] is False
