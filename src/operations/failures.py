"""Bounded diagnostics: never serialize exception messages, SQL or locals."""
from pathlib import Path


def diagnostic(error):
    leaves = []
    def visit(exc):
        if len(leaves) >= 6:
            return
        if isinstance(exc, BaseExceptionGroup):
            for child in exc.exceptions:
                visit(child)
        else:
            frames=[]; tb=exc.__traceback__
            while tb:
                filename=tb.tb_frame.f_code.co_filename.replace('\\','/')
                if '/src/' in filename or filename.endswith('/api.py'):
                    frames.append(Path(filename).name+':'+str(tb.tb_lineno))
                tb=tb.tb_next
            code=getattr(exc,'sqlstate',None)
            busy=code in {'55P03','57014','53300','40001','40P01'} or (
                type(exc).__module__.startswith('psycopg_pool') and type(exc).__name__=='PoolTimeout')
            leaves.append({'kind':type(exc).__name__[:80], 'sqlstate':code if isinstance(code,str) and len(code)==5 and code.isalnum() else None,
                           'frames':frames[-4:], 'busy':busy})
    visit(error)
    return {'busy':bool(leaves) and all(e['busy'] for e in leaves), 'errors':leaves}
