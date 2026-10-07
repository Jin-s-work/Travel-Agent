"""Pure civil-time rules. Missing dates/clock times never acquire a guessed deadline."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from src.itineraries.intervals import resolve_local

VERSION = 'reservation_rules_v1'

def release_timing(calculation, zone, now=None):
    """A display state only; passage of time never invents an opening/availability."""
    now=now or datetime.now(timezone.utc)
    if calculation['due_precision']=='instant':
        return 'past' if datetime.fromisoformat(calculation['due_at'])<=now else 'future'
    if calculation['due_precision']=='date':
        day=date.fromisoformat(calculation['due_date']);today=now.astimezone(ZoneInfo(zone)).date()
        return 'past' if day<today else 'due_today' if day==today else 'future'
    return 'unknown'

def calculate(rule, visit_date, zone):
    out = dict(calculation_version=VERSION,due_at=None,due_date=None,due_precision='unknown',reason='RULE_UNCONFIRMED')
    if not isinstance(rule,dict): return out
    try:
        visit=date.fromisoformat(visit_date); kind=rule.get('type')
        if rule.get('timezone') != zone: return {**out,'reason':'FACILITY_TIMEZONE_UNCONFIRMED'}
        if kind=='rolling_days':
            n=rule['days_before_visit']
            if type(n) is not int or not 0<=n<=730: raise ValueError()
            target=visit-timedelta(days=n)
        elif kind=='monthly_release':
            offset=rule['target_month_offset'];day=rule['release_day']
            if type(offset) is not int or not 0<=offset<=24 or type(day) is not int: raise ValueError()
            month=visit.year*12+visit.month-1-offset
            # date() rejects Feb 30/31. Never clamp to month-end.
            target=date(month//12,month%12+1,day)
        elif kind=='fixed_datetime': target=date.fromisoformat(rule['explicit_local_date'])
        else: return {**out,'reason':'UNSUPPORTED_RULE'}
        if target>visit: return {**out,'reason':'RELEASE_AFTER_VISIT'}
        out.update(due_date=target.isoformat(),due_precision='date',reason=None)
        local_time=rule.get('explicit_local_time')
        if local_time:
            value=resolve_local(target.isoformat()+'T'+local_time,zone)
            if value['state']!='satisfied': return {**out,'due_date':None,'due_precision':'unknown','reason':value['reason_code']}
            out.update(due_at=value['instant'],due_precision='instant')
        return out
    except (KeyError,TypeError,ValueError,OverflowError): return {**out,'reason':'INVALID_RULE_DATE'}
