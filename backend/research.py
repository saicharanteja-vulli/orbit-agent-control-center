"""Build source provenance from successful tool records, before approval hashing."""
import json
import re
from .db import connect

URL = re.compile(r'https?://[^\s<>\[\]"`]+')


def source_report(run_id, content):
    with connect() as db:
        run = db.execute('SELECT task,enabled_tools FROM runs WHERE id=?', (run_id,)).fetchone()
        rows = db.execute('SELECT tool,status,result FROM actions WHERE run_id=? ORDER BY sequence', (run_id,)).fetchall()
    research = any(r['tool'] == 'web_search' for r in rows) or (
        run and 'web_search' in json.loads(run['enabled_tools']) and
        re.search(r'\b(research|search|sourced)\b', run['task'], re.I))
    if not research:
        return content, []
    sources = []
    for row in rows:
        if row['tool'] != 'read_webpage' or row['status'] != 'succeeded':
            continue
        result = json.loads(row['result'] or '{}')
        url = result.get('url', '')
        if url.startswith('https://') and result.get('text') and url not in sources:
            sources.append(url)
    if not sources:
        raise ValueError('Research reports require at least one successfully read webpage. Read a source before proposing delivery.')
    # Small models sometimes double-escape an entire Markdown document.
    # Only repair that recognizable case; never reinterpret escapes in normal text.
    if '\n' not in content and '\\n' in content:
        content = content.replace('\\n', '\n')
    cited = {m.group().rstrip(').,;:') for m in URL.finditer(content)}
    unsupported = cited - set(sources)
    if unsupported:
        raise ValueError('Report contains URLs not supported by successfully read pages. Read those sources or remove these URLs: ' + ', '.join(sorted(unsupported)))
    content = content.rstrip() + '\n\n## Sources consulted\n\n' + '\n'.join(
        f'- [Source {i}]({url})' for i, url in enumerate(sources, 1)) + '\n'
    return content, sources
