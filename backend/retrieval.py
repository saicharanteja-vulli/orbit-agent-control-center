"""Bounded document retrieval with an explicit BM25 baseline and optional dense fusion.

No network access occurs unless an Ollama embedder is explicitly supplied. Chunk IDs
are content-addressed; character offsets refer to the original uploaded text.
"""
from collections import Counter
import hashlib
import math
import re
import time

STOP = set('a an the is are was were be been to of in on for from and or with what which how does do can i my it this that by at as'.split())
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
TOP_K = 4


def terms(text):
    return [word for word in re.findall(r'[^\W_]+', text.casefold()) if word not in STOP]


def chunk_document(document):
    text = document['text']
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            boundary = text.rfind('\n', start + CHUNK_SIZE // 2, end)
            if boundary < 0:
                boundary = text.rfind(' ', start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        body = text[start:end]
        if body.strip():
            identity = f"{document['id']}:{start}:{end}:" + hashlib.sha256(body.encode()).hexdigest()
            chunks.append({'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                           'document_id': document['id'], 'title': document['title'],
                           'text': body, 'start': start, 'end': end,
                           'line_start': text.count('\n', 0, start) + 1,
                           'line_end': text.count('\n', 0, end) + 1})
        if end == len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def cosine(left, right):
    if len(left) != len(right) or not left:
        raise ValueError('Embedding dimensions do not match.')
    if any(not math.isfinite(x) for x in (*left, *right)):
        raise ValueError('Non-finite embedding.')
    denominator = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    if denominator == 0:
        raise ValueError('Zero embedding.')
    return sum(x*y for x, y in zip(left, right)) / denominator


class Index:
    def __init__(self, documents):
        self.chunks = [chunk for document in documents for chunk in chunk_document(document)]
        self.counts = [Counter(terms(c['title'] + '\n' + c['text'])) for c in self.chunks]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average = sum(self.lengths) / max(1, len(self.lengths))
        self.frequency = Counter(term for count in self.counts for term in count)

    def bm25(self, question):
        scores = []
        for count, length in zip(self.counts, self.lengths):
            score = 0.0
            for term in set(terms(question)):
                freq = count[term]
                if not freq:
                    continue
                idf = math.log(1 + (len(self.chunks) - self.frequency[term] + .5) / (self.frequency[term] + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (.25 + .75 * length / max(1, self.average)))
            scores.append(score)
        return scores

    def search(self, question, strategy='bm25', embedder=None, k=TOP_K):
        if strategy not in {'bm25', 'dense', 'hybrid'}:
            raise ValueError('Unknown retrieval strategy.')
        if not 1 <= k <= 10:
            raise ValueError('Choose between one and ten passages.')
        started = time.perf_counter()
        lexical = self.bm25(question)
        dense = [0.0] * len(self.chunks)
        if strategy != 'bm25' and self.chunks:
            if embedder is None:
                raise ValueError('Dense and hybrid retrieval require a configured embedding model.')
            vectors = embedder([question] + [c['title'] + '\n' + c['text'] for c in self.chunks])
            if len(vectors) != len(self.chunks) + 1:
                raise ValueError('Embedding provider returned the wrong number of vectors.')
            dense = [cosine(vectors[0], vector) for vector in vectors[1:]]
        lexical_rank = sorted((i for i, s in enumerate(lexical) if s > 0), key=lambda i: (-lexical[i], self.chunks[i]['id']))
        dense_rank = sorted((i for i, s in enumerate(dense) if s > 0), key=lambda i: (-dense[i], self.chunks[i]['id']))
        if strategy == 'hybrid':
            # Reciprocal rank fusion is intentionally fixed before held-out evaluation.
            scores = Counter()
            for ranking in (lexical_rank, dense_rank):
                for rank, index in enumerate(ranking, 1):
                    scores[index] += 1 / (60 + rank)
            ranking = sorted(scores, key=lambda i: (-scores[i], self.chunks[i]['id']))
        else:
            scores = lexical if strategy == 'bm25' else dense
            ranking = lexical_rank if strategy == 'bm25' else dense_rank
        hits = [dict(self.chunks[i], score=round(scores[i], 8), bm25=round(lexical[i], 6),
                     cosine=round(dense[i], 6), source_id=f'S{rank}')
                for rank, i in enumerate(ranking[:k], 1)]
        return {'strategy': strategy, 'hits': hits,
                'retrieval_ms': round((time.perf_counter() - started) * 1000, 3),
                'chunk_count': len(self.chunks)}
