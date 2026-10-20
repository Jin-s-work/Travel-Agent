#!/usr/bin/env python3
"""Validate repository presentation links, packages and synchronized scripts.

Standard library only. No network, provider calls or environment file loading.
Native Keynote visual checks are recorded separately in docs/presentation/REVIEW.md.
"""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRESENTATION = ROOT / 'docs/presentation'
NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}

def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', value))

def headings(text):
    return {re.sub(r'[^\w\- ]', '', line.lower()).replace(' ', '-')
            for line in re.findall(r'^#{1,6} (.+)$', text, re.M)}

def validate(presentation=PRESENTATION, prefix='going-class-presentation', expected_slides=10, embedded_movie=False, duration=510, concise=False):
    names=tuple(name for name in ('README.md','CREDITS.md','REVIEW.md') if (presentation/name).exists())
    docs = [ROOT/'README.md'] + [presentation/name for name in names]
    errors, count = [], 0
    for doc in docs:
        body = doc.read_text()
        targets = re.findall(r'!?\[[^\]]*\]\(([^\s)]+)', body)
        targets += re.findall(r'(?:src|href)="([^"]+)"', body)
        for value in targets:
            parsed=urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            target=(doc.parent/unquote(parsed.path)).resolve() if parsed.path else doc
            count += 1
            if not target.exists():
                errors.append(f'{doc.relative_to(ROOT)}: missing {value}')
            elif parsed.fragment and target.suffix=='.md':
                if unquote(parsed.fragment) not in headings(target.read_text()):
                    errors.append(f'{doc.relative_to(ROOT)}: missing heading {value}')
    data=json.loads((presentation/'slides-content.json').read_text())
    assert len(data)==expected_slides, 'Unexpected slide count'
    assert sum(x['seconds'] for x in data)==duration, 'Timing allocation changed'
    assert not (presentation/'SCRIPT.md').exists(), 'Private script must not be published'
    assert all('notes' not in row and 'cue' not in row for row in data), 'Private notes in slide data'
    with zipfile.ZipFile(presentation/f'{prefix}.pptx') as z:
        assert z.testzip() is None
        if concise:
            assert not any(n.endswith(('.mp4','.mov')) for n in z.namelist()), 'Unexpected video'
        slides=[n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)]
        notes=[n for n in z.namelist() if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml',n)]
        assert len(slides)==len(notes)==expected_slides
        size=ET.fromstring(z.read('ppt/presentation.xml')).find('p:sldSz',NS)
        assert (size.get('cx'),size.get('cy'))==('12192000','6858000')
        for i,d in enumerate(data,1):
            note=normalized(''.join(ET.fromstring(z.read(f'ppt/notesSlides/notesSlide{i}.xml')).itertext()))
            assert not note, f'Slide {i}: private presenter notes remain'
            text=normalized(''.join(ET.fromstring(z.read(f'ppt/slides/slide{i}.xml')).itertext()))
            if concise:
                assert '30+90+30' not in text and '150분' not in text, f'Slide {i}: removed calculation remains'
            assert normalized(d['title']) in text, f'Slide {i}: title differs'
            if not (embedded_movie and i == expected_slides):
                assert normalized(d['subtitle']) in text, f'Slide {i}: subtitle differs'
        if embedded_movie:
            movie=(presentation/'going-demo.mp4').read_bytes()
            assert any(z.read(n)==movie for n in z.namelist() if n.endswith('.mp4')), 'Embedded PPTX movie differs'
    with zipfile.ZipFile(presentation/f'{prefix}.key') as z:
        assert z.testzip() is None
        assert any(n.startswith('Index/') for n in z.namelist())
        if concise:
            assert not any(n.endswith(('.mp4','.mov')) for n in z.namelist()), 'Unexpected Keynote video'
        if embedded_movie:
            assert any(z.read(n)==movie for n in z.namelist() if n.endswith('.mp4')), 'Embedded Keynote movie differs'
    assert not errors, '\n'.join(errors)
    files = docs + [presentation/n for n in (f'{prefix}.pptx',f'{prefix}.key','slides-content.json','preview.webp')]
    return {'scope':'Document/package consistency only; not product tests or live quality',
            'passed':True,'local_links_checked':count,'slides':expected_slides,'pptx_slides_without_notes':expected_slides,
            'embedded_movie_matches_standalone':embedded_movie,
            'target_duration_seconds':duration,'actual_spoken_duration_measured':False,
            'native_keynote_visual_review':'Separate from this package checker',
            'files':{str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest',action='store_true')
    version=parser.add_mutually_exclusive_group()
    version.add_argument('--v8',action='store_true',help='Validate 12-slide, 10-minute presentation without references or video')
    version.add_argument('--v7',action='store_true',help='Validate latest 11-slide presentation and embedded demo')
    version.add_argument('--v4',action='store_true',help='Validate new evidence-led presentation, preserving previous files')
    args=parser.parse_args()
    presentation=PRESENTATION/'v8' if args.v8 else PRESENTATION/'v7' if args.v7 else PRESENTATION/'v4' if args.v4 else PRESENTATION
    prefix='going-v8-class-presentation' if args.v8 else 'going-v7-class-presentation' if args.v7 else 'going-v4-class-presentation' if args.v4 else 'going-class-presentation'
    result=validate(presentation,prefix,12 if args.v8 else 11 if args.v7 else 10,args.v7,600 if args.v8 else 510,args.v8)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.write_manifest:
        (presentation/'validation.json').write_text(text)
    print(text,end='')
