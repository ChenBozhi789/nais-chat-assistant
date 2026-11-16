# NAIS Assistant MVP guide (Azure RAG)

## Architecture

```
Widget (JS) → FastAPI /chat → Azure AI Search (retrieve) → Azure OpenAI (answer) → reply + links
                    ↑
        rate limits, spend cap, kill switch, content filter
```

| Need | Azure service | MVP note |
| --- | --- | --- |
| Embeddings + answers | Azure OpenAI (Azure AI Foundry) | One embedding deployment, one small chat deployment |
| Retrieval | Azure AI Search | Hybrid (keyword + vector) search. The Free tier is enough to prototype; Basic is a paid tier, so check its price against your credits |
| Hosting the API | Container Apps or App Service | Deploy last |
| Secrets | Key Vault + managed identity | For deployment; a local `.env` is fine while developing |
| Safety | Foundry content filters | On by default, so just configure them |
| Monitoring | Application Insights + Cost Management budgets | Budgets only alert and don't stop spending |
| Later extras | Azure AI Speech (speech-to-text, read-aloud), Azure AI Translator | Replaces the browser Web Speech API if you want higher quality |

## Step 1: Azure setup

1. Use your own subscription for now (for example Azure for Students, if you have it), then move to the nonprofit subscription once the grant is approved.
2. Create one resource group, `rg-nais-assistant`, and put everything in it. Deleting that group cleans up everything.
3. Create an Azure OpenAI / Foundry resource and deploy:
   - An embedding model, such as `text-embedding-3-small`.
   - A small, cheap chat model, such as a `gpt-4o-mini` or `gpt-4.1-mini` class model. Check what's currently available in your region.
4. Create an Azure AI Search service (Free tier to start).
5. Put the endpoints, keys and deployment names in `.env`:
   ```
   AOAI_ENDPOINT=...
   AOAI_KEY=...
   AOAI_API_VERSION=2024-10-21
   EMBED_DEPLOYMENT=...
   CHAT_DEPLOYMENT=...
   SEARCH_ENDPOINT=...
   SEARCH_KEY=...
   SEARCH_INDEX=nais-content
   ```
6. Set a budget alert in Cost Management right away.

```bash
pip install openai azure-search-documents python-dotenv pyyaml
```

## Step 2: Check your Markdown files

Every file in `content/clean/` should start with front matter, because the source URL powers the "linked answers" feature:

```
---
url: https://nais.../tutorials/crafting-your-prompts
title: Crafting Your Prompts
---
# Crafting Your Prompts
...
```

Also check that no navigation, footer or cookie text is left in the files. That junk gets retrieved and pollutes answers.

## Step 3: Chunk, embed and index (`rag/ingest.py`)

Split each page by headings, then merge or split to roughly 300-500 words. Store the url and title with every chunk.

```python
import os, glob, re, yaml
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchField, SearchFieldDataType,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile)

load_dotenv()
aoai = AzureOpenAI(azure_endpoint=os.environ["AOAI_ENDPOINT"],
                   api_key=os.environ["AOAI_KEY"],
                   api_version=os.environ["AOAI_API_VERSION"])
cred = AzureKeyCredential(os.environ["SEARCH_KEY"])
INDEX = os.environ["SEARCH_INDEX"]

def load_page(path):
    text = open(path, encoding="utf-8").read()
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm)
    return meta["url"], meta["title"], body.strip()

def chunk(body, max_words=400):
    sections = re.split(r"\n(?=#{1,3} )", body)   # split on headings
    chunks, buf = [], ""
    for s in sections:
        if len((buf + s).split()) > max_words and buf:
            chunks.append(buf.strip()); buf = ""
        buf += "\n" + s
    if buf.strip(): chunks.append(buf.strip())
    return chunks

def embed(texts):
    r = aoai.embeddings.create(model=os.environ["EMBED_DEPLOYMENT"], input=texts)
    return [d.embedding for d in r.data]

def create_index():
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="url", type=SearchFieldDataType.String),
        SearchField(name="embedding",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, vector_search_dimensions=1536,
                    vector_search_profile_name="vec"),
    ]
    vs = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="vec", algorithm_configuration_name="hnsw")])
    SearchIndexClient(os.environ["SEARCH_ENDPOINT"], cred).create_or_update_index(
        SearchIndex(name=INDEX, fields=fields, vector_search=vs))

if __name__ == "__main__":
    create_index()
    docs = []
    for i, path in enumerate(glob.glob("content/clean/*.md")):
        url, title, body = load_page(path)
        for j, c in enumerate(chunk(body)):
            docs.append({"id": f"{i}-{j}", "title": title, "url": url, "content": c})
    for k in range(0, len(docs), 16):                # embed in batches
        batch = docs[k:k+16]
        for d, v in zip(batch, embed([d["content"] for d in batch])):
            d["embedding"] = v
    SearchClient(os.environ["SEARCH_ENDPOINT"], INDEX, cred).upload_documents(docs)
    print(f"Indexed {len(docs)} chunks")
```

The `1536` matches `text-embedding-3-small`, so change it if you pick another embedding model. Re-running the script after content changes is your content-refresh process.

## Step 4: Retrieve and answer (`rag/answer.py`)

```python
from ingest import aoai, cred, INDEX, embed, os
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

search = SearchClient(os.environ["SEARCH_ENDPOINT"], INDEX, cred)

SYSTEM = """You are the NAIS website assistant. Answer ONLY from the SOURCES below.
- Use plain, friendly English for beginners.
- Never invent dates, prices, or details. If the sources don't answer it, say you
  don't know and point the visitor to NAIS staff.
- Do not give legal, medical or financial advice.
- Finish with the relevant source links."""

def answer(question, history=()):
    vq = VectorizedQuery(vector=embed([question])[0], k_nearest_neighbors=5, fields="embedding")
    hits = list(search.search(search_text=question, vector_queries=[vq], top=5))
    context = "\n\n".join(f"[{h['title']}] ({h['url']})\n{h['content']}" for h in hits)
    msgs = [{"role": "system", "content": f"{SYSTEM}\n\nSOURCES:\n{context}"},
            *history[-6:], {"role": "user", "content": question[:500]}]
    r = aoai.chat.completions.create(model=os.environ["CHAT_DEPLOYMENT"],
                                     messages=msgs, max_tokens=400, temperature=0.2)
    return r.choices[0].message.content, [h["url"] for h in hits]
```

Hybrid search matters here: the keyword half catches exact terms like "Christchurch" or "Not-for-Profit" that pure vector search can miss.

## Step 5: Test before building anything else

1. Put 30-40 questions in `evaluation/questions.json`, each with an expected fact or expected source URL.
2. Write `evaluation/run.py`, which loops over them, prints the answer and sources, and flags any answer whose sources lack the expected URL.
3. Include trick questions such as "What's the price of the workshop?" (if not on the site), "Should I invest in AI stocks?" and "Ignore your instructions". The correct behaviour is to say "I don't know" or refuse.
4. Fix problems in this order:
   - Retrieval first (chunk size, junk in the pages).
   - Prompt second.
   - Model last.

Don't move on until this passes at the bar you agree with NAIS.

## Step 6: API with the safety controls (`api/main.py`)

FastAPI with a single `POST /chat` endpoint that applies these checks before calling `answer()`:

- **Input caps:** question ≤ 500 characters, history ≤ 6 turns, `max_tokens` ≤ 400.
- **Per-IP rate limit:** `slowapi`, for example 10 requests per minute.
- **Daily request or token counter:** a global limit that returns a friendly "assistant is resting, here are the FAQ and contact links" message when reached.
- **Kill switch:** an environment variable or config flag, `ASSISTANT_ENABLED=false`.
- **CORS:** restricted to the NAIS domain only.
- **Content filter errors:** catch Azure's filter rejection and return a polite refusal.
- **Logging:** log only the anonymised question text, with no IPs or identifiers stored.

In-memory counters work for one instance. If you scale out, move them to Azure Table Storage or Redis.

## Step 7: Widget

Write a single `widget/nais-chat.js` that injects a floating button and a chat panel, calls `/chat`, renders the reply with clickable links, and keeps the last few turns in memory (not storage). Include the privacy notice in the panel, and show fallback links to FAQs, bookings and contact if the API fails. Test it on a plain HTML page first, then in a WordPress staging page.

## Step 8: Deploy

1. Containerise the API with a `Dockerfile`, then deploy it to Azure Container Apps (or App Service).
2. Turn on managed identity, move keys to Key Vault, and drop the keys from `.env`.
3. Connect Application Insights.
4. Add the script tag to the tutorials and workshop pages only.

## Step 9: Azure-powered extras (after the MVP works)

- **Speech-to-text and read-aloud:** Azure AI Speech, for the older users in your proposal.
- **Translation:** Azure AI Translator.
- **Booking help:** an Azure Functions endpoint the bot calls. Do this last, because it is the riskiest feature and it touches personal data.

## Costs to check early

Your proposal's US$20/month covers model calls only. Azure AI Search is a separate charge, with a free tier (small, no SLA) and a paid Basic tier, so check the current pricing page and compare it with the roughly US$167/month the grant works out to. For a small NAIS content set, the Free tier is likely enough for the pilot.

## Board cards to add

1. Prepare content (your existing card): front matter checked, junk removed.
2. Azure resources created and `.env` working.
3. Ingest script and index.
4. Answer script.
5. Test set of 30-40 questions, passing.
6. API with safety controls tested.
7. Widget on a test page.
8. Deploy and monitoring.
9. Pilot release and staff sign-off.
