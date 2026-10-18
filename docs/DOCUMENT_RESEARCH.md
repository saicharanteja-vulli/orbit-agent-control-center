# Document research

Orbit now supports question answering over an explicitly selected, private text corpus.
This workflow does not search the public web or execute tool proposals. It uses the
existing durable queue, worker, cancellation handling, account isolation and report approval.

## Use it

1. Start Ollama and pull a chat model, for example `ollama pull qwen3:4b`.
2. For semantic search, also run `ollama pull all-minilm` and set
   `ORBIT_EMBEDDING_MODEL=all-minilm` in the API and worker environment.
   Without it, keyword search remains available with no embedding dependency.
3. Start Orbit with `./start.sh`, sign in and open **Documents**.
4. Import a UTF-8 `.txt` or `.md` file, or paste text with a title. Save it and select
   the documents you want to use. PDFs and images are deliberately unsupported.
5. Ask a specific question and choose keyword, semantic or combined search. All
   generation runs on the configured Ollama server; no hosted generation API is used.
6. Open the result's evidence sections. Compare each claim to its exact source quote
   and inspect other retrieved passages. Approve export only after reviewing the answer.

For Docker, the Compose environment passes `ORBIT_EMBEDDING_MODEL` to the API and
worker. Pull both models into the Compose Ollama volume before using the feature:

```sh
docker compose exec ollama ollama pull qwen3:4b
docker compose exec ollama ollama pull all-minilm
```

## Architecture

```text
Private document library -- selected inputs --> immutable run snapshot in SQLite
                                                       |
                                                queued local worker
                                                       |
                                1,200-character passages / 180-character overlap
                                                       |
                              BM25 | dense cosine | reciprocal-rank fusion (60)
                                                       |
                                             top 4 source passages
                                                       |
                                        structured local Ollama answer
                                                       |
                                      exact quote and source-ID validation
                                                       |
                            cited answer or abstention --> human export review
```

The implementation is deliberately small and inspectable. BM25 is implemented in
`backend/retrieval.py`; dense retrieval uses Ollama's `/api/embed` endpoint and cosine
similarity; combined search applies reciprocal-rank fusion. No relevance labels or
benchmark answers enter the retrieval or generation pipeline. Fusion weights, chunk
sizes and prompt parameters are fixed rather than optimized against the test split.

`backend/document_qa.py` requests schema-constrained claims and exact supporting
quotes. Unknown source IDs, non-verbatim quotes, malformed claim structures and
uncontrolled citation links fail validation. Empty lexical retrieval skips generation.
An unavailable embedding service fails the run visibly; it never silently changes the
retrieval strategy. Provider errors are failures, not successful abstentions.

The model can decline when information is missing. Invalid citation responses are
withheld with a separate reason. A citation that passes validation is **not** proof of
semantic support: a model can still cite an irrelevant sentence or misinterpret it.
The UI and exports disclose that limit. Prompt injection is tested, not claimed solved.

## Data and access boundaries

- Every library read, selection and deletion checks the current account. An administrator
  cannot bypass another account's document ownership. Existing session, CSRF and viewer
  restrictions also apply to the new endpoints.
- Inputs are JSON text, not filesystem paths or URLs. Imports are restricted to 40,000
  characters and 60 KB of UTF-8 text per document, 30 documents and 500,000 characters
  per account. The existing 64 KB HTTP body limit also applies to encoded requests.
- Chunks carry content-addressed IDs, original character offsets and line ranges.
  Titles and text are passed to the model as data, not as permissions to call tools.
- Selected documents are captured with content hashes when a task is queued, so editing
  the library cannot change evidence halfway through a run. Library removal does not
  erase earlier run snapshots or results. The removal confirmation explains this.
  There is currently no complete account-data erasure UI.
- Embeddings are cached only within a provider instance/run and keyed by model digest
  and text hash. There is no shared cross-account vector cache. Benchmark runs reuse
  corpus embeddings across questions within one strategy; product runs rebuild them.
- Research exports include cited text and source titles. Export approval is enabled by
  default in this workspace. API callers can choose to disable report-export approval,
  as in the existing task API; tool writes still have their separate mandatory gate.
- A cancelled run cannot commit a result. A non-streamed model request already in
  flight may continue consuming local compute until it completes or times out.

## API additions

- `GET /api/documents`: list owned document metadata, without source text.
- `POST /api/documents`: save `{"title":"...","text":"..."}`; identical text is deduplicated per owner.
- `GET /api/documents/{id}`: owned text and chunk offsets.
- `DELETE /api/documents/{id}`: remove from library; earlier snapshots remain.
- `GET /api/research/config`: enabled retrieval choices.
- `POST /api/runs`: adds `research_mode`, `document_ids`, `retrieval_strategy`.

Example task fields, in addition to normal authenticated/CSRF request headers:

```json
{
  "task": "What does the policy require before a webhook is sent?",
  "agent": "analyst",
  "mode": "ollama",
  "model": "qwen3:4b",
  "enabled_tools": [],
  "research_mode": true,
  "document_ids": ["your-owned-document-id"],
  "retrieval_strategy": "hybrid",
  "approval_required": true
}
```

## Reliability correction discovered during verification

The earlier permission-hardening helper opened and closed existing SQLite database
and sidecar files outside SQLite. On POSIX, closing another descriptor can release the
process's database locks. Concurrent API/worker testing exposed corruption in an
isolated test database. The helper now creates new files exclusively and uses path
metadata operations for existing files, without opening and closing them. A regression
test holds a SQLite writer transaction, runs permission hardening, and confirms a
second process still cannot acquire the writer lock.

This mechanism is documented in [SQLite's corruption guide](https://www.sqlite.org/howtocorrupt.html#_posix_advisory_locks_canceled_by_a_separate_thread_doing_close_).
The change preserves owner-only database/directory permissions.

## Limits and next validation

This remains a single-server beta. The evaluation corpus is small, fictional and
English-only. It does not validate a general research assistant, PDF extraction,
long-document synthesis or production security. Lexical search can miss paraphrases;
dense search can retrieve semantically similar but wrong passages; fusion is not
guaranteed to improve either. Compare measured results before choosing a default.

The next meaningful validation is an independently labeled, permissioned corpus and
questions from real users, with claim-level support review and repeated model runs.

References: [Ollama embeddings](https://docs.ollama.com/capabilities/embeddings),
[structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
