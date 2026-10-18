# Orbit document research benchmark

Run: 2026-10-18T04:11:45.182658+00:00 · Split: dev · Dataset: orbit-docqa-v1

This is a small, hand-authored fictional policy benchmark. Results are diagnostic and do not establish production accuracy.

| Strategy | Cases | Errors | Document recall@4 | MRR | Retrieval p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 10 | 0 | 1.0 | 0.9286 | 0.07 |
| dense | 10 | 0 | 1.0 | 0.8333 | 92.11 |
| hybrid | 10 | 0 | 1.0 | 0.9286 | 217.67 |

| Strategy | Automatic fact/abstention proxy | Unanswerable abstention | Answerable abstention | Citation document relevance | Total p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 0.6 | 0.0 | 0.1429 | 0.5294 | 4022.89 |
| dense | 0.5 | 0.0 | 0.2857 | 0.5833 | 2459.77 |
| hybrid | 0.6 | 0.0 | 0.1429 | 0.5333 | 3301.68 |

## Metric definitions and limits

- Recall@4 measures the fraction of expected source documents present in the top four chunks, averaged over answerable cases. MRR uses the first relevant chunk. These are document-level relevance labels, not sentence-level labels.
- Automatic answer pass requires every expected fact alias in generated claim text, or abstention on an unanswerable case. It can miss paraphrases and cannot detect every unsupported extra claim. It is not a semantic accuracy score.
- Citation document relevance measures references to labeled relevant documents. Exact quote provenance is enforced in the product; neither metric establishes entailment. Manually review the raw claims and quotes.
- Provider errors count as failed answers. Invalid citations produce abstention and are counted separately. High abstention can reduce usefulness.
- Timing includes local model loading and a per-strategy in-memory embedding cache. The first query per strategy embeds the corpus; later queries reuse embeddings. Live product runs currently rebuild that cache per run. No dollar cost is estimated for local inference.
- Published test questions are a fixed split, not a secret benchmark. Do not tune on them; version the dataset and use new external questions for future validation.

## Failures

- bm25 / dev03 (direct): recall=1.0, abstained=True, fact checks=[False]
- bm25 / dev04 (negative): recall=None, abstained=False, fact checks=[]
- bm25 / dev07 (negative): recall=None, abstained=False, fact checks=[]
- bm25 / dev10 (negative): recall=None, abstained=False, fact checks=[]
- dense / dev03 (direct): recall=1.0, abstained=True, fact checks=[False]
- dense / dev04 (negative): recall=None, abstained=False, fact checks=[]
- dense / dev05 (paraphrase): recall=1.0, abstained=True, fact checks=[False, False]
- dense / dev07 (negative): recall=None, abstained=False, fact checks=[]
- dense / dev10 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / dev03 (direct): recall=1.0, abstained=True, fact checks=[False]
- hybrid / dev04 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / dev07 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / dev10 (negative): recall=None, abstained=False, fact checks=[]

The companion JSON contains every question, retrieved passage, answer, quote, timing and usage record.
