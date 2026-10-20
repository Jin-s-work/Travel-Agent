#!/usr/bin/env python3
"""Pre-publish scan for the public Jin-s-work/Travel-Agent repository.

    python scripts/check_publish_safety.py              # tracked files, any finding fails
    python scripts/check_publish_safety.py --ci         # secrets fail, paths/scripts warn
    python scripts/check_publish_safety.py --untracked  # also files not yet added
    python scripts/check_publish_safety.py --range origin/main..HEAD   # added lines in commits

Only `path:line rule` is printed, never the matched value. A finding is a reason
to look before pushing; nothing here edits a file. Once pushed, a public commit
cannot be removed without a force-push, which this project does not allow.
"""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# Rules split by consequence. Secret rules fail in --ci; exposure rules warn.
SECRET_RULES = (
    ('openai_key', re.compile(r'\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}')),
    ('anthropic_key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}')),
    ('private_key_block', re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----')),
    ('google_api_key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('github_token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})')),
    ('apify_token', re.compile(r'\bapify_api_[A-Za-z0-9]{20,}')),
    ('supabase_secret_key', re.compile(r'\bsb_secret_[A-Za-z0-9_-]{20,}')),
    ('jwt_token', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')),
)
DB_URL = re.compile(r'\bpostgres(?:ql)?(?:\+\w+)?://(?P<user>[^:@/\s\'"`]+):(?P<password>[^@\s\'"`]+)@(?P<host>[^/:\s\'"`?]+)')
PLACEHOLDER = re.compile(r'[<>\[\]{}$*]|\.\.\.|password|passwd|secret|example|changeme|your|xxx', re.I)
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}
USER_PATH = re.compile(r'/Users/(?!<|\$|\{|Shared/)[^/\s\'"`<>]+/')

BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.key', '.zip', '.gz',
                   '.woff', '.woff2', '.ttf', '.otf', '.sqlite3', '.db', '.pyc', '.mp4', '.mov'}
ENV_EXAMPLE = re.compile(r'(?:^|\.)(?:example|sample|template)$')
MAX_BYTES = 5_000_000


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def listed(untracked):
    paths = [p for p in git('ls-files', '-z').split('\0') if p]
    if untracked:
        paths += [p for p in git('ls-files', '-z', '--others', '--exclude-standard').split('\0') if p]
    return sorted(set(paths))


def path_rules(path):
    """Rules that depend on the file name alone, so evicted/binary files are covered too."""
    name = PurePosixPath(path).name
    found = []
    if name.startswith('.env') and not ENV_EXAMPLE.search(name):
        found.append(('fail', 'tracked_env_file'))
    if re.fullmatch(r'docs/presentation/(?:.+/)?SCRIPT\.md', path):
        found.append(('warn', 'presentation_script_tracked'))
    return found


def line_rules(line):
    found = [('fail', name) for name, pattern in SECRET_RULES if pattern.search(line)]
    for match in DB_URL.finditer(line):
        if PLACEHOLDER.search(match['password']):
            continue
        found.append(('warn', 'db_url_loopback_password') if match['host'] in LOOPBACK else ('fail', 'db_url_password'))
    if USER_PATH.search(line):
        found.append(('warn', 'absolute_user_path'))
    return found


def scan_text(label, text):
    for number, line in enumerate(text.splitlines(), 1):
        for level, rule in line_rules(line):
            yield level, f'{label}:{number}', rule


def scan_files(paths):
    for path in paths:
        for level, rule in path_rules(path):
            yield level, path, rule
        file = ROOT / path
        if file.suffix.lower() in BINARY_SUFFIXES or not file.is_file() or file.stat().st_size > MAX_BYTES:
            continue
        data = file.read_bytes()
        if b'\0' in data[:8192]:
            continue
        yield from scan_text(path, data.decode('utf-8', 'replace'))


def scan_range(spec):
    """Added lines of every commit in the range, so a secret removed later still counts."""
    commit = path = None; number = 0
    for raw in git('log', '-p', '--no-color', '--no-ext-diff', '-U0', '--format=commit %H', spec).splitlines():
        if raw.startswith('commit ') and len(raw) == 47:
            commit = raw[7:19]; continue
        if raw.startswith('+++ '):
            path = raw[6:] if raw.startswith('+++ b/') else None
            if path:
                for level, rule in path_rules(path):
                    yield level, f'{commit}:{path}', rule
            continue
        hunk = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)', raw)
        if hunk:
            number = int(hunk[1]); continue
        if path and raw.startswith('+') and PurePosixPath(path).suffix.lower() not in BINARY_SUFFIXES:
            for level, rule in line_rules(raw[1:]):
                yield level, f'{commit}:{path}:{number}', rule
            number += 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ci', action='store_true', help='fail only on secret rules; paths and scripts warn')
    parser.add_argument('--untracked', action='store_true', help='also scan untracked, non-ignored files')
    parser.add_argument('--range', help='scan lines added by commits in a git revision range instead')
    parser.add_argument('paths', nargs='*', help='limit the scan to these repository paths')
    args = parser.parse_args(argv)
    if args.range:
        findings = list(scan_range(args.range))
    else:
        findings = list(scan_files(args.paths or listed(args.untracked)))
    counts = {}
    for level, where, rule in findings:
        print(f'{"FAIL" if level == "fail" else "WARN"} {where} {rule}')
        counts[rule] = counts.get(rule, 0) + 1
    fails = sum(1 for level, _, _ in findings if level == 'fail')
    warns = len(findings) - fails
    summary = ', '.join(f'{rule}={count}' for rule, count in sorted(counts.items())) or 'none'
    print(f'publish safety: {fails} fail, {warns} warn ({summary})')
    return 1 if fails or (warns and not args.ci) else 0


if __name__ == '__main__':
    sys.exit(main())
