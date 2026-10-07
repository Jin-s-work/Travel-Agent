"""Safe RFC5545 snapshots and reviewed, non-committal inquiry templates (no LLM)."""
from datetime import datetime,timezone
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import hashlib
import json


def escape(value):
    return str(value).replace('\\','\\\\').replace('\r\n','\n').replace('\r','\n').replace('\n','\\n').replace(';','\\;').replace(',','\\,')

def fold(line):
    rows=[];part='';size=0
    for char in line:
        length=len(char.encode('utf-8'))
        if size+length>75: rows.append(part);part=' ';size=1
        part+=char;size+=length
    rows.append(part)
    return '\r\n'.join(rows)

def calendar(task):
    calc=task['calculation']
    if calc['due_precision']=='unknown': raise ValueError('UNKNOWN_DATE')
    stamp=datetime.fromisoformat(task['updated_at']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    local_due=(datetime.fromisoformat(calc['due_at']).astimezone(ZoneInfo(task['timezone'])).isoformat()
               if calc['due_precision']=='instant' else calc['due_date']+' (시각 미확인)')
    party=task.get('party') or {}
    description=['예약 준비 알림. 예약 확정이 아닙니다. 다운로드 시점의 snapshot이며 자동 갱신되지 않습니다.',
        '시설 시간대: '+task['timezone'], '시설 현지 준비 시점: '+local_due,
        '희망 방문일: '+task.get('visit_date','미확인'),
        f"성인 {party.get('adults','미확인')}명 · 아동 {len(party.get('children',[]))}명"]
    source=((task.get('rule') or {}).get('source') or {}).get('url')
    if source:
        parts=urlsplit(source)
        # Calendar files are shareable snapshots. Do not export URL credentials,
        # query tokens or fragments, even from an otherwise approved source.
        if parts.scheme=='https' and parts.hostname and not parts.username and not parts.password:
            description.append('공식 확인: '+urlunsplit((parts.scheme,parts.netloc,parts.path,'','')))
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Travel Agent//Preparation v1//KO','CALSCALE:GREGORIAN','BEGIN:VEVENT',
        'UID:'+task['id']+'@travel-agent.invalid','SEQUENCE:'+str(task['version']), 'DTSTAMP:'+stamp,
        'SUMMARY:'+escape(task['title']), 'DESCRIPTION:'+escape('\n'.join(description))]
    if calc['due_precision']=='instant':
        instant=datetime.fromisoformat(calc['due_at']).astimezone(timezone.utc)
        lines.append('DTSTART:'+instant.strftime('%Y%m%dT%H%M%SZ'))
    else: lines.append('DTSTART;VALUE=DATE:'+calc['due_date'].replace('-',''))
    if task['status']=='cancelled': lines.append('STATUS:CANCELLED')
    lines+=['END:VEVENT','END:VCALENDAR']
    return ('\r\n'.join(fold(line) for line in lines)+'\r\n').encode('utf-8')


def inquiry(task, language):
    slots={k:task.get(k) for k in ('title','place_name','visit_date','requested_time','party','requests','request_keys','timezone')}
    party=slots['party']; ages=', '.join('?' if c['age'] is None else str(c['age']) for c in party['children']) or '—'
    day=slots['visit_date'];time=slots['requested_time'] or '—';title=slots.get('place_name') or slots['title'];adults=party['adults'];children=len(party['children'])
    # User supplied requests are quoted as original content, never invented translations.
    common=f"{day} / {time} / {slots['timezone']}"
    texts={
      'ja':f'こんにちは。{title}への訪問を希望しています。\n希望日時（未確定）: {common}\n大人: {adults}名、子ども: {children}名（年齢: {ages}）。\nこの条件で予約は可能でしょうか。予約方法と子どもの利用条件を教えてください。これは問い合わせであり、予約の確定ではありません。',
      'es':f'Hola. Nos gustaría visitar {title}.\nFecha y hora deseadas (sin confirmar): {common}\nAdultos: {adults}; menores: {children} (edades: {ages}).\n¿Sería posible reservar con estas condiciones? ¿Cuál es el procedimiento y la política para menores? Esto es una consulta, no una reserva confirmada.',
      'ca':f'Hola. Ens agradaria visitar {title}.\nData i hora desitjades (sense confirmar): {common}\nAdults: {adults}; infants: {children} (edats: {ages}).\nSeria possible reservar amb aquestes condicions? Quin és el procediment i la política per als infants? És una consulta, no una reserva confirmada.'}
    korean=f"{title} 방문을 희망합니다.\n희망 날짜·시간(미확정): {common}\n성인 {adults}명, 아동 {children}명 (나이: {ages}).\n이 조건의 예약 가능 여부, 예약 방법과 아동 이용 조건을 문의합니다. 예약 확정 문구가 아닙니다."
    translations={
      'vegetarian':('ベジタリアン料理は可能ですか。','¿Hay opciones vegetarianas?','Hi ha opcions vegetarianes?','채식 메뉴가 가능한가요?'),
      'vegan':('ヴィーガン料理は可能ですか。','¿Hay opciones veganas?','Hi ha opcions veganes?','비건 메뉴가 가능한가요?'),
      'nut_allergy':('ナッツアレルギーへの対応と交差接触のリスクを教えてください。','¿Pueden atender una alergia a frutos secos y explicar el riesgo de contacto cruzado?','Podeu atendre una al·lèrgia als fruits secs i explicar el risc de contacte encreuat?','견과류 알레르기 대응과 교차 접촉 위험을 알려 주세요.'),
      'gluten_free':('グルテンフリーの食事と交差接触について確認したいです。','Quisiera consultar las opciones sin gluten y el contacto cruzado.','Voldria consultar les opcions sense gluten i el contacte encreuat.','글루텐 제외 식사와 교차 접촉 여부를 문의합니다.'),
      'step_free':('段差のない入口と移動経路はありますか。','¿Hay una entrada y un recorrido sin escalones?','Hi ha una entrada i un recorregut sense esglaons?','입구와 이동 경로에 계단 없는 접근이 가능한가요?')}
    for key in slots.get('request_keys') or []:
        phrases=translations[key];texts[language]+='\n'+phrases[{'ja':0,'es':1,'ca':2}[language]];korean+='\n'+phrases[3]
    if slots['requests']:
        suffix='\n[Original request / 원문 요청 · 번역 확인 필요]\n'+slots['requests'];texts[language]+=suffix;korean+=suffix
    return {'language':language,'text':texts[language],'korean':korean,'slots':slots,'template_version':'inquiry_v1',
        'slots_hash':hashlib.sha256(json.dumps(slots,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'unconfirmed':['requested_time'] if not slots['requested_time'] else [],'external_action':'none','editable_copy_only':True}


def validate_draft(task,candidate,language):
    """Fail closed to the reviewed template if any structured slot or wording changes.

    No provider is invoked in v1; this boundary also rejects future unsafe model
    adapters instead of treating fluent text as evidence of a reservation.
    """
    safe=inquiry(task,language)
    matched=isinstance(candidate,dict) and all(candidate.get(k)==safe[k] for k in ('slots','text','korean','template_version','slots_hash'))
    return {**safe,'template_fallback':not matched}
