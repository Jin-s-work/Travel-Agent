"""Bounded Apify Compass adapter; HTTP only, zero hidden retries.

Public documentation was checked on 2026-10-01. Dataset offsets are NOT Google
Maps pagination evidence. Newest means an unverified source time basis until a
separate contract review establishes publication vs last-edit ordering.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import re
import time
import httpx

from .reviews import CollectionRequest, ProviderCapabilities, ProviderPage, ReviewCollectionProvider, ReviewProviderError


class ApifyReviewCollectionProvider(ReviewCollectionProvider):
    name = 'apify'
    adapter_version = 'apify-compass-v1'
    source_pagination_observable = False  # Dataset offsets expose no Google source-page receipts.
    actor = 'compass~google-maps-reviews-scraper'
    API = 'https://api.apify.com/v2'
    FIELDS = 'reviewId,text,textTranslated,originalLanguage,translatedLanguage,publishedAtDate,scrapedAt,stars,reviewOrigin,placeId'

    def __init__(self, token: str, *, build: str, client: httpx.Client | None = None,
                 contract_verified: bool = False, sort_basis: str = 'unknown',
                 continuity_verified: bool = False, page_size: int = 50):
        if not re.fullmatch(r'\d+\.\d+\.\d+',build):
            raise ValueError('Pinned numeric Actor build is required')
        if sort_basis not in ('published_at','edited_at','unknown'):
            raise ValueError('Invalid sort time basis')
        if not 1 <= page_size <= 100:
            raise ValueError('Invalid bounded dataset page size')
        self._token,self.build = token,build
        self.client = client or httpx.Client(timeout=30,follow_redirects=False)
        self.contract_verified = contract_verified
        self.page_size = page_size
        self.capabilities = ProviderCapabilities(sort_basis=sort_basis,
            continuity_verified=continuity_verified,source_pagination_visible=False)

    @property
    def configured(self):
        return bool(self._token)

    def _request(self, method, path, request, *, params=None, payload=None):
        if not self.configured:
            raise ReviewProviderError('provider_not_configured')
        # Fixed trusted origin. Never follow redirects to token-exfiltrating hosts.
        headers = {'Authorization':f'Bearer {self._token}','Accept':'application/json'}
        deadline=time.monotonic()+min(request.max_elapsed_seconds,30)
        try:
            with self.client.stream(method,self.API+path,params=params,json=payload,
                                    headers=headers,timeout=min(request.max_elapsed_seconds,30),
                                    follow_redirects=False) as response:
                if method=='DELETE' and response.status_code in (204,404):
                    return {'deleted':True,'already_absent':response.status_code==404},dict(response.headers)
                if response.status_code in (401,403):
                    raise ReviewProviderError('provider_blocked')
                if 300 <= response.status_code < 400:
                    raise ReviewProviderError('provider_blocked')
                if response.status_code >= 400:
                    retry = response.status_code in (429,500,502,503,504) and method=='GET'
                    retry_after = response.headers.get('retry-after','0')
                    try:
                        retry_after = float(retry_after)
                    except ValueError:
                        retry_after = 0
                    raise ReviewProviderError('provider_error',retryable=retry,retry_after=retry_after)
                content_type=response.headers.get('content-type','').split(';')[0].strip().lower()
                if content_type!='application/json' and not content_type.endswith('+json'):
                    raise ReviewProviderError('parse_error')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic()>=deadline:
                        raise ReviewProviderError('time_cap')
                    raw.extend(chunk)
                    if len(raw)>request.max_response_bytes:
                        raise ReviewProviderError('parse_error')
                try:
                    data=json.loads(raw)
                except (ValueError,UnicodeDecodeError) as exc:
                    raise ReviewProviderError('parse_error') from exc
                return data,dict(response.headers)
        except httpx.HTTPError as exc:
            # POST outcome can be charged despite a lost response. Caller retains
            # unknown reservation and never automatically starts another run.
            raise ReviewProviderError('provider_outcome_unknown') from exc

    @staticmethod
    def _id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9]{1,80}',value):
            raise ReviewProviderError('parse_error')
        return value

    def _metadata(self, value):
        if not isinstance(value,dict) or not isinstance(value.get('data'),dict):
            raise ReviewProviderError('parse_error')
        data=value['data']
        result={'run_id':self._id(data.get('id')), 'status':str(data.get('status','UNKNOWN'))[:32],
                'dataset_id':self._id(data['defaultDatasetId']) if data.get('defaultDatasetId') else None,
                'build_id':data.get('buildId') if isinstance(data.get('buildId'),str) else None,
                'build_number':data.get('buildNumber') if isinstance(data.get('buildNumber'),str) else self.build,
                'usage_usd':None,'usage_final':False,'charged_event_counts':{}}
        total=data.get('usageTotalUsd')
        if isinstance(total,(int,float)) and not isinstance(total,bool) and Decimal(str(total)).is_finite() and total>=0:
            result['usage_usd']=str(total)
        # A terminal response can still have preliminary charges. Separate
        # reconciliation is necessary; a successful poll is not final billing.
        events=data.get('chargedEventCounts',{})
        if isinstance(events,dict):
            result['charged_event_counts']={k:v for k,v in events.items()
                if k in ('review-scraped','apify-actor-start') and type(v) is int and v>=0}
        return result

    def start(self, request):
        if request.provider!=self.name or request.adapter_version!=self.adapter_version:
            raise ValueError('Provider adapter contract mismatch')
        if not re.fullmatch(r'(?:ChIJ|GhIJ)[A-Za-z0-9_-]{23}',request.external_place_id):
            raise ValueError('Verified supported Google Place ID required; URL search is separate')
        if Decimal(request.max_total_charge_usd)<=0:
            raise ValueError('Explicit positive reviewed remote charge cap required')
        body={'placeIds':[request.external_place_id], 'maxReviews':request.max_review_records,
              'reviewsSort':'newest','reviewsStartDate':request.requested_start,
              'reviewsFilterString':'','reviewsOrigin':'google','personalData':False,
              'language':request.website_locale}
        params={'build':self.build,'timeout':request.max_elapsed_seconds,'memory':1024,
                'maxTotalChargeUsd':request.max_total_charge_usd,'restartOnError':'false','waitForFinish':0}
        data,_=self._request('POST',f'/acts/{self.actor}/runs',request,params=params,payload=body)
        return self._metadata(data)

    def poll(self, remote, request):
        data,_=self._request('GET',f'/actor-runs/{self._id(remote["run_id"])}',request)
        return self._metadata(data)

    def abort(self, remote, request):
        data,_=self._request('POST',f'/actor-runs/{self._id(remote["run_id"])}/abort',request,
                             params={'gracefully':'true'})
        result=self._metadata(data)
        result['cancellation_requested']=True
        result['charges_cancelled']=False
        return result

    def delete_dataset(self, remote, request):
        data,_=self._request('DELETE',f'/datasets/{self._id(remote.get("dataset_id"))}',request)
        return {'deleted':bool(data.get('deleted')),'already_absent':bool(data.get('already_absent'))}

    def delete_run(self, remote, request):
        # Dataset deletion is separate: callers checkpoint each deletion receipt
        # and must not discard the dataset ID before that deletion succeeds.
        data,_=self._request('DELETE',f'/actor-runs/{self._id(remote.get("run_id"))}',request)
        return {'deleted':bool(data.get('deleted')),'already_absent':bool(data.get('already_absent'))}

    def canonical_record(self, row, request):
        if not isinstance(row,dict):
            raise ReviewProviderError('parse_error')
        if row.get('reviewOrigin') not in ('Google','google'):
            raise ReviewProviderError('parse_error')
        if row.get('placeId')!=request.external_place_id:
            raise ReviewProviderError('place_identity_mismatch')
        original=row.get('text') if isinstance(row.get('text'),str) else None
        translated=row.get('textTranslated') if isinstance(row.get('textTranslated'),str) else None
        if original and len(original)>30000 or translated and len(translated)>30000:
            raise ReviewProviderError('parse_error')
        present=bool(original and original.strip() or translated and translated.strip())
        no_text_confirmed='text' in row and 'textTranslated' in row and row['text'] in (None,'') and row['textTranslated'] in (None,'')
        presence='present' if present else 'rating_only' if no_text_confirmed else 'unextractable'
        review_id=row.get('reviewId')
        if not isinstance(review_id,str) or not 0<len(review_id)<=1000:
            review_id=None
        date=row.get('publishedAtDate')
        # Relative publishAt is deliberately not converted to an exact date.
        from .reviews import parse_timestamp
        exact=bool(parse_timestamp(date))
        stars=row.get('stars')
        if not isinstance(stars,(int,float)) or isinstance(stars,bool) or not 0<=stars<=5:
            stars=None
        return {'provider_review_id':review_id,'original_text':original,'translated_text':translated,
            'original_language':row.get('originalLanguage') if isinstance(row.get('originalLanguage'),str) else None,
            'original_language_verified':self.contract_verified,
            'original_separation_verified':self.contract_verified,
            'text_presence':presence,'published_at':date if exact else None,'edited_at':None,
            'date_precision':'exact' if exact else 'unknown','published_at_precision':'exact' if exact else 'unknown',
            'edited_at_precision':'unknown','rating':stars,'rating_scale':5,
            'fetched_at':datetime.now(timezone.utc).isoformat(),'source':'google_maps'}

    def fetch_page(self, remote, cursor, request):
        if remote.get('status')!='SUCCEEDED':
            raise ReviewProviderError('provider_not_ready')
        dataset=self._id(remote.get('dataset_id'))
        if cursor is None:
            offset=0
        elif isinstance(cursor,str) and re.fullmatch(r'offset:\d{1,6}',cursor):
            offset=int(cursor.split(':')[1])
        else:
            raise ReviewProviderError('cursor_expired')
        limit=min(self.page_size,request.max_review_records)
        payload,headers=self._request('GET',f'/datasets/{dataset}/items',request,
            params={'format':'json','offset':offset,'limit':limit,'fields':self.FIELDS,
                    'clean':'false','desc':'false'})
        if not isinstance(payload,list) or len(payload)>limit:
            raise ReviewProviderError('parse_error')
        records=[self.canonical_record(row,request) for row in payload]
        try:
            total=int(headers['x-apify-pagination-total'])
        except (KeyError,ValueError):
            total=None
        exhausted=total is not None and offset+len(payload)>=total
        next_cursor=None if exhausted else f'offset:{offset+len(payload)}'
        limitations=['SOURCE_PAGINATION_NOT_VISIBLE']
        if self.capabilities.sort_basis=='unknown':limitations.append('NEWEST_TIME_BASIS_UNVERIFIED')
        if not self.contract_verified:
            limitations.append('ORIGINAL_FIELDS_UNVERIFIED')
        if total is None:
            limitations.append('DATASET_TOTAL_UNKNOWN')
        return ProviderPage(records,next_cursor,exhausted,
            sort_basis=self.capabilities.sort_basis,continuity_verified=self.capabilities.continuity_verified,
            remote_ref=remote['run_id'],capabilities=self.capabilities.to_dict(),limitations=limitations,
            usage={'dataset_records_received':len(payload),'usage_final':False})
