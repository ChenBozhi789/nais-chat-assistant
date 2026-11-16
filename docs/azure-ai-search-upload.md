# Upload reviewed NAIS chunks to Azure AI Search

This guide uses the current project design: a Microsoft Foundry embedding
deployment creates vectors, and Azure AI Search stores the chunk text, source metadata, and vectors. The
FastAPI chat endpoint comes later. Start with the reviewed **basic pages**;
upload tutorials only after their separate review.

The older [MVP guide](nais-assistant-mvp-guide.md) shows a `load_page()` example
that expects YAML front matter. Our actual reviewed input is JSONL, so use
`rag/ingest.py` here instead. It uploads the exact `content` values in JSONL
and does not re-run the chunker.

## 1. Prepare Azure resources

In the [Azure portal](https://portal.azure.com/):

1. Open your **Microsoft Foundry** resource. Under **Build → Models**, check
   that an **embedding model deployment** exists. A chat model deployment
   alone cannot create embeddings. If needed, deploy an embedding model such
   as `text-embedding-3-small` from the model catalog. Open the deployment's
   details or **Code** tab and copy its OpenAI-compatible **inference base
   URL**, resource API key, and **deployment name**. The base URL should end
   in `/openai/v1/`; both `<resource>.services.ai.azure.com/openai/v1/` and
   `<resource>.openai.azure.com/openai/v1/` are supported. Do not paste the
   Foundry **project URL** ending `/api/projects/<project>` here. The SDK's
   `model=` parameter uses the deployment name.
2. Create or open an **Azure AI Search** service. For a small pilot, the Free
   tier is an option if your subscription is eligible. Copy its endpoint from
   the service Overview page; it has the form
   `https://<service-name>.search.windows.net`.
3. In the Search service, open **Settings → Keys**. Copy an **admin key** for
   local index creation/upload, and a **query key** for the later read-only
   backend. Do not put either key in the widget or commit them to Git.

The Python script creates the index schema itself. You do not need a separate
Azure OpenAI resource. You do not need to use the
portal's *Import data* wizard or upload the Markdown/PDF files separately.
See Microsoft's [Foundry deployment](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/deploy-foundry-models),
[Foundry endpoint](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints),
[Search service creation](https://learn.microsoft.com/en-us/azure/search/search-create-service-portal)
and [key guidance](https://learn.microsoft.com/en-us/azure/search/search-security-api-keys).

## 2. Configure local environment

From the repository root, create `.env` from `.env.example` if it does not
already exist. Fill in these values with your own resource information:

```dotenv
FOUNDRY_OPENAI_BASE_URL=https://<foundry-resource>.services.ai.azure.com/openai/v1/
FOUNDRY_API_KEY=<foundry-resource-key>
FOUNDRY_EMBEDDING_DEPLOYMENT=<embedding-deployment-name>
AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
AZURE_SEARCH_ADMIN_KEY=<search-admin-key>
AZURE_SEARCH_QUERY_KEY=<search-query-key>
AZURE_SEARCH_INDEX=nais-content
```

Keep `.env` local; it is Git-ignored. The chat deployment can be filled in
when implementing answer generation. Use the exact embedding **deployment
name**, not just its underlying model name. The upload script obtains the
embedding dimension from the model response and creates a matching vector
field. The `/openai/v1/` route does not require an `api-version` parameter.
Microsoft's [Foundry endpoint guide](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints)
and [vector quickstart](https://learn.microsoft.com/en-us/azure/search/search-get-started-vector)
explain this requirement.

Install the project dependencies in the existing virtual environment:

```bash
cd /Users/bozhi/developer/nais-chat-assistant
.venv/bin/pip install -r backend/requirements.txt
```

## 3. Choose and validate the reviewed chunk file

The first upload should use:

`content/chunks/basic-pages/chunks.jsonl`

Each nonblank line is one chunk with `id`, `content`, `title`, `url`, and other
metadata. The `id` is the Azure Search document key; keep it stable when
editing a chunk. Use the accompanying `chunk-review.html` to locate lines.
If you edit `content`, update or clear `review_flags` **only after reviewing
the issue**. Do not rerun `rag.chunk_basic_pages` after manual edits: that
command regenerates and overwrites the JSONL.

Run the local validation first. It makes no Azure calls:

```bash
.venv/bin/python -m rag.ingest
```

This prints the chunk count and any flagged IDs. Fix invalid JSON, missing
metadata, duplicate IDs, or unreviewed flags before uploading. Blank lines
between JSON objects are allowed.

Before uploading all chunks, you can make **one embedding request** to verify
the Foundry endpoint, key, and model deployment. This does not write to Search:

```bash
.venv/bin/python -m rag.ingest --probe-embedding
```

It prints only the vector dimension, not the vector values or your key.

## 4. Embed, create the index, and upload

After local review, run:

```bash
.venv/bin/python -m rag.ingest --upload
```

The script sends each chunk's `content` to the configured Foundry
embedding deployment, creates `AZURE_SEARCH_INDEX` if absent, then uploads
batches of documents containing `id`, `title`, `url`, `source_file`, `section`,
`content`, and `embedding`. It checks every per-document upload result. It
refuses an upload while `review_flags` remain; `--allow-flagged` is available
only if you have deliberately accepted those chunks.

Creating embeddings and using Azure AI Search can incur charges. This script
does **not** upload anything unless you add `--upload`. It does not print API
keys or the full chunk content. It uses the admin key only on the backend.
Microsoft's [vector quickstart](https://learn.microsoft.com/en-us/azure/search/search-get-started-vector)
shows the same create-index → upload-documents sequence.

## 5. Verify the uploaded data

In the Azure portal, open your Search service → **Search management → Indexes**
→ the `nais-content` index (or your configured name) → **Search explorer**.
Switch to JSON view and run a text query:

```json
{
  "search": "book a free session",
  "select": "id,title,url,content",
  "top": 5
}
```

Confirm that results contain a relevant chunk and its NAIS source URL. The
[Search explorer guide](https://learn.microsoft.com/en-us/azure/search/search-explorer)
describes this portal check.

For a hybrid query from Python, generate the question embedding with the
**same deployment** used at upload time and combine it with keyword search:

```python
import os
from dotenv import load_dotenv
from openai import OpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

load_dotenv()
question = "How can I book a free session?"
foundry = OpenAI(
    base_url=os.environ["FOUNDRY_OPENAI_BASE_URL"],
    api_key=os.environ["FOUNDRY_API_KEY"],
)
vector = foundry.embeddings.create(
    model=os.environ["FOUNDRY_EMBEDDING_DEPLOYMENT"], input=question
).data[0].embedding
search = SearchClient(
    endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
    index_name=os.environ["AZURE_SEARCH_INDEX"],
    credential=AzureKeyCredential(os.environ["AZURE_SEARCH_QUERY_KEY"]),
)
for hit in search.search(
    search_text=question,
    vector_queries=[VectorizedQuery(vector=vector, k_nearest_neighbors=4, fields="embedding")],
    select=["id", "title", "url", "content"],
    top=4,
):
    print(hit["id"], hit["title"], hit["url"])
```

This is the query shape the later `backend/answer.py` should use. It combines keyword and
vector results, as described in Microsoft's [hybrid search guide](https://learn.microsoft.com/en-us/azure/search/hybrid-search-how-to-query).

To run the existing question set against the Search index in one batch, use:

```bash
.venv/bin/python -m rag.evaluate_retrieval --only loc hours
.venv/bin/python -m rag.evaluate_retrieval
```

The default uses hybrid keyword + vector retrieval. Add `--keyword-only` to
compare with Search explorer without making embedding calls. The report is
written to `evaluation/retrieval-results.json` and contains the top four chunks
for each question. For the 28 `fact` cases, `PASS` means the expected source
URL and expected text appeared somewhere in the retrieved context; it does
not prove the final answer will be correct. The seven `unknown` and four
`refuse` cases are marked `INSPECT` because only a generated answer can be
graded for those behaviors. Since all chunks were uploaded, review the
`unknown` cases whose notes assumed some chunks would be held back.

## 6. Update content and run the test questions

- Editing an existing chunk and uploading it again with the same `id` replaces
  that Search document and vector. The script re-embeds all chunks each run,
  so repeated uploads invoke the Foundry embedding deployment again.
- Removing a chunk from the local JSONL does **not** remove its earlier copy
  from the Search index. Delete the stale document explicitly or start with a
  new index name when rebuilding a pilot index. See Microsoft's
  [delete-document guidance](https://learn.microsoft.com/en-us/azure/search/search-how-to-delete-documents).
- When the basic-page retrieval is correct, implement the grounded answer
  function and run the 39 questions in `evaluation/questions.json` against it.
  The current `backend/run.py` runner calls `/chat`, so it cannot run until
  the API exists; use a direct answer-function test runner for the guide's
  Step 5. Then wrap the same answer function in the Step 6 FastAPI `/chat`
  endpoint. The API is a later interface to the same index; it is not the
  initial upload mechanism.

## Common problems

| Symptom | Check |
| --- | --- |
| `Missing .env settings` | Endpoint, key, index name, and embedding deployment name are filled in. |
| `403` from Search | Use the admin key for index creation/upload; enable key authentication if using keys. |
| Index dimensions differ | The index was created with another embedding model or dimension. Use the original model or a new index name. |
| Search returns stale chunks | Same-ID upload updates documents, but removed IDs need explicit deletion. |
| Search returns the wrong page | Inspect the chunk content and source URL first; then tune retrieval before the answer prompt. |
