"""python -m evaluation.run --split dev --strategies bm25,dense,hybrid --generate"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import httpx
from backend.document_qa import Ollama, answer_question, SYSTEM, Answer
from backend.retrieval import Index, CHUNK_SIZE, CHUNK_OVERLAP, TOP_K

ROOT = Path(__file__).resolve().parent


def normalize(text):
    return re.sub(r'\s+', ' ', text.casefold()).strip()


def retrieval_metrics(hits, relevant):
    if not relevant:
        return {'recall_at_k': None, 'mrr': None}
    ids = [hit['document_id'] for hit in hits]
    return {'recall_at_k': len(set(ids) & set(relevant)) / len(set(relevant)),
            'mrr': next((1 / rank for rank, identifier in enumerate(ids, 1) if identifier in relevant), 0)}


def score_answer(result, case):
    expected_abstention = not case['relevant']
    output = normalize(' '.join(claim['text'] for claim in result['claims']))
    fact_checks = [any(normalize(alias) in output for alias in group) for group in case['facts']]
    forbidden = any(normalize(term) in output for term in case.get('forbidden', []))
    citations = [c for claim in result['claims'] for c in claim['citations']]
    source_map = {h['source_id']: h['document_id'] for h in result['retrieval']['hits']}
    relevant_citations = sum(source_map.get(c['source_id']) in case['relevant'] for c in citations)
    # This is an explicit lexical proxy, not a judge of entailment or full correctness.
    passed = result['abstained'] if expected_abstention else not result['abstained'] and all(fact_checks)
    return {'expected_abstention': expected_abstention, 'abstained': result['abstained'],
            'fact_checks': fact_checks, 'automatic_fact_or_abstention_pass': bool(passed and not forbidden),
            'forbidden_output': forbidden, 'citation_count': len(citations),
            'relevant_citation_count': relevant_citations,
            'invalid_response_abstention': result['reason'] == 'invalid_citation_or_response'}


def mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def percentile(values, fraction):
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 2) if values else None


def summarize(rows, generation):
    ok = [row for row in rows if not row.get('error')]
    summary = {'cases': len(rows), 'errors': len(rows)-len(ok),
               'recall_at_k': mean(row.get('recall_at_k') for row in rows),
               'mrr': mean(row.get('mrr') for row in rows),
               'retrieval_p50_ms': percentile([row['retrieval_ms'] for row in ok], .5),
               'retrieval_p95_ms': percentile([row['retrieval_ms'] for row in ok], .95)}
    if generation:
        positives = [r for r in rows if not r['expected_abstention']]
        negatives = [r for r in rows if r['expected_abstention']]
        citation_count = sum(r.get('citation_count', 0) for r in rows)
        summary.update({
            'automatic_fact_or_abstention_pass_rate': mean(int(r.get('automatic_fact_or_abstention_pass', False)) for r in rows),
            'answerable_fact_pass_rate': mean(int(r.get('automatic_fact_or_abstention_pass', False)) for r in positives),
            'unanswerable_abstention_rate': mean(int(r.get('abstained', False)) for r in negatives),
            'answerable_abstention_rate': mean(int(r.get('abstained', False)) for r in positives),
            'invalid_response_abstentions': sum(r.get('invalid_response_abstention', False) for r in rows),
            'citation_document_relevance': round(sum(r.get('relevant_citation_count', 0) for r in rows) / citation_count, 4) if citation_count else None,
            'citation_count': citation_count,
            'total_p50_ms': percentile([row['total_ms'] for row in ok], .5),
            'total_p95_ms': percentile([row['total_ms'] for row in ok], .95),
            'prompt_tokens': sum(r.get('usage', {}).get('prompt_tokens', 0) for r in rows),
            'completion_tokens': sum(r.get('usage', {}).get('completion_tokens', 0) for r in rows),
        })
    return summary


def markdown(report):
    lines = ['# Orbit document research benchmark', '',
             f"Run: {report['created_at']} · Split: {report['split']} · Dataset: {report['dataset_version']}", '',
             'This is a small, hand-authored fictional policy benchmark. Results are diagnostic and do not establish production accuracy.', '',
             '| Strategy | Cases | Errors | Document recall@4 | MRR | Retrieval p95 ms |',
             '|---|---:|---:|---:|---:|---:|']
    for name, summary in report['summary'].items():
        lines.append(f"| {name} | {summary['cases']} | {summary['errors']} | {summary['recall_at_k']} | {summary['mrr']} | {summary['retrieval_p95_ms']} |")
    if report['generation']:
        lines.extend(['', '| Strategy | Automatic fact/abstention proxy | Unanswerable abstention | Answerable abstention | Citation document relevance | Total p95 ms |',
                      '|---|---:|---:|---:|---:|---:|'])
        for name, summary in report['summary'].items():
            lines.append(f"| {name} | {summary['automatic_fact_or_abstention_pass_rate']} | {summary['unanswerable_abstention_rate']} | {summary['answerable_abstention_rate']} | {summary['citation_document_relevance']} | {summary['total_p95_ms']} |")
    lines.extend(['', '## Metric definitions and limits', '',
        '- Recall@4 measures the fraction of expected source documents present in the top four chunks, averaged over answerable cases. MRR uses the first relevant chunk. These are document-level relevance labels, not sentence-level labels.',
        '- Automatic answer pass requires every expected fact alias in generated claim text, or abstention on an unanswerable case. It can miss paraphrases and cannot detect every unsupported extra claim. It is not a semantic accuracy score.',
        '- Citation document relevance measures references to labeled relevant documents. Exact quote provenance is enforced in the product; neither metric establishes entailment. Manually review the raw claims and quotes.',
        '- Provider errors count as failed answers. Invalid citations produce abstention and are counted separately. High abstention can reduce usefulness.',
        '- Timing includes local model loading and a per-strategy in-memory embedding cache. The first query per strategy embeds the corpus; later queries reuse embeddings. Live product runs currently rebuild that cache per run. No dollar cost is estimated for local inference.',
        '- Published test questions are a fixed split, not a secret benchmark. Do not tune on them; version the dataset and use new external questions for future validation.', '', '## Failures', ''])
    failures = [r for r in report['rows'] if r.get('error') or (report['generation'] and not r.get('automatic_fact_or_abstention_pass')) or r.get('recall_at_k', 1) not in (None, 1)]
    lines.extend(f"- {r['strategy']} / {r['id']} ({r['category']}): " + (r.get('error') or f"recall={r.get('recall_at_k')}, abstained={r.get('abstained')}, fact checks={r.get('fact_checks')}") for r in failures)
    if not failures:
        lines.append('No failures under these automatic checks; manual review is still required.')
    lines.extend(['', 'The companion JSON contains every question, retrieved passage, answer, quote, timing and usage record.', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'benchmark.json')
    parser.add_argument('--split', choices=['dev', 'test'], default='dev')
    parser.add_argument('--strategies', default='bm25')
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--model', default='qwen3:1.7b')
    parser.add_argument('--embedding-model', default='all-minilm')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    strategies = args.strategies.split(',')
    if any(s not in {'bm25', 'dense', 'hybrid'} for s in strategies):
        parser.error('Strategies must be bm25, dense or hybrid.')
    data = json.loads(args.dataset.read_text())
    cases = [case for case in data['cases'] if case['split'] == args.split]
    if not cases:
        parser.error('Selected split is empty.')
    identifiers = [c['id'] for c in data['cases']]
    if len(set(identifiers)) != len(identifiers):
        parser.error('Duplicate case IDs.')
    doc_ids = {d['id'] for d in data['documents']}
    if any(not set(c['relevant']) <= doc_ids for c in cases):
        parser.error('Unknown relevance label.')
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'dataset_version': data['version'],
              'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              'split': args.split, 'generation': args.generate, 'model': args.model,
              'embedding_model': args.embedding_model, 'python': sys.version,
              'platform': platform.platform(), 'machine': platform.machine(),
              'config': {'chunk_size': CHUNK_SIZE, 'overlap': CHUNK_OVERLAP, 'top_k': TOP_K, 'rrf_constant': 60, 'temperature': 0, 'seed': 42},
              'rows': [], 'summary': {}}
    report['generation_prompt'] = SYSTEM
    report['answer_schema'] = Answer.model_json_schema()
    report['generation_settings'] = {'stream': False, 'think': False, 'num_predict': 1800, 'num_ctx': 8192}
    source_paths = sorted(path for folder in ('backend', 'evaluation', 'frontend/src', 'tests')
                          for path in Path(folder).rglob('*') if path.is_file()
                          and path.suffix in {'.py', '.tsx', '.css', '.json'} and 'results' not in path.parts)
    report['source_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    try:
        report['git_revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        report['git_diff_sha256'] = hashlib.sha256(subprocess.check_output(['git', 'diff', 'HEAD'])).hexdigest()
    except (OSError, subprocess.CalledProcessError):
        report['git_revision'] = None
    if args.generate or any(s != 'bm25' for s in strategies):
        provider = Ollama(args.model, args.embedding_model)
        response = httpx.get(provider.url + '/api/tags', timeout=5)
        response.raise_for_status()
        report['installed_models'] = response.json()['models']
    index = Index(data['documents'])
    report['chunk_count'] = len(index.chunks)
    for strategy in strategies:
        provider = Ollama(args.model, args.embedding_model)
        for case in cases:
            row = {k: case[k] for k in ('id', 'category', 'question', 'relevant', 'facts')}
            row.update(strategy=strategy, expected_abstention=not case['relevant'])
            started = time.perf_counter()
            try:
                if args.generate:
                    result = answer_question(data['documents'], case['question'], provider, strategy, capture_raw=True)
                    retrieval = result['retrieval']
                    row.update(score_answer(result, case), result=result, usage=result['usage'], total_ms=result['total_ms'])
                else:
                    retrieval = index.search(case['question'], strategy, provider.embed if strategy != 'bm25' else None)
                row.update(retrieval_metrics(retrieval['hits'], case['relevant']), retrieval_ms=retrieval['retrieval_ms'], retrieval=retrieval)
            except Exception as exc:
                row.update(error=f'{type(exc).__name__}: {exc}', total_ms=round((time.perf_counter()-started)*1000, 3),
                           recall_at_k=0 if case['relevant'] else None, mrr=0 if case['relevant'] else None)
            report['rows'].append(row)
            print(strategy, case['id'], row.get('error') or ('pass' if row.get('automatic_fact_or_abstention_pass') else 'recorded'), flush=True)
        report['summary'][strategy] = summarize([r for r in report['rows'] if r['strategy'] == strategy], args.generate)
        report['summary'][strategy]['embedding_requests'] = provider.embedding_requests
        report['summary'][strategy]['embedding_tokens'] = provider.embedding_tokens
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    args.output.with_suffix('.md').write_text(markdown(report))
    print(json.dumps(report['summary'], indent=2))
    return int(any(summary['errors'] for summary in report['summary'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
