# 从零练习 Chroma

这个目录只用来学习本地向量检索。NAIS MVP 的正式方案仍按项目 `AGENTS.md` 使用 Azure AI Search。这里使用的 71 条数据来自 `content/embedding/basic-pages/`，是已有的 Azure AI Search 导出文件；导入 Chroma 时**不会重新生成文档向量**。

## 1. 先弄清四个概念

一条 Chroma 记录可以看成：

| 字段 | 作用 | 本项目的例子 |
| --- | --- | --- |
| `id` | 唯一标识一段内容 | `christchurch-workshops-1` |
| `document` | 可阅读、可展示的正文 | `content` 字段 |
| `embedding` | 表示正文语义的数字向量 | 长度为 1536 的 `embedding` 字段 |
| `metadata` | 可过滤、可显示的来源信息 | `title`、`url`、`source_file`、`section` |

**Collection** 是一组这样的记录。检索时把问题也转换为向量，Chroma 找出距离最近的记录。Chroma 返回的是相关片段，**不是最终答案**；要做 RAG 回答，还需要把片段交给生成模型并约束它只根据来源作答。

向量的数字没有可直接阅读的含义。真正重要的是：查询向量与文档向量应由**同一个 embedding 模型／部署**生成；仅仅维度相同还不够。

## 2. 准备环境

在仓库根目录运行：

```bash
cd /Users/bozhi/developer/nais-chat-assistant
.venv/bin/python -c "import chromadb; print(chromadb.__version__)"
```

当前项目的 `.venv` 已装 `chromadb`、`openai` 和 `python-dotenv`。如果你在新的虚拟环境练习，可先创建环境，再安装这三个包：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install 'chromadb>=1.5,<2' 'openai>=1.50,<3' 'python-dotenv>=1,<2'
```

以下命令都假定当前目录是仓库根目录。Chroma 的本地数据库会写在 `evaluation/chroma/db/`；该目录已被 Git 忽略。

## 3. 用两个二维向量理解基本操作

```bash
.venv/bin/python evaluation/chroma/chroma_basics.py
```

预期第一行是 Python 书，距离约 `0.02`；第二行是烹饪书，距离约 `1.62`。打开 [`chroma_basics.py`](chroma_basics.py) 对照阅读：

1. `EphemeralClient()` 创建**内存**数据库，程序结束后数据消失。
2. `create_collection("books", embedding_function=None)` 建立集合。`None` 表示 Chroma 不替你调用任何 embedding 模型。
3. `add(ids=..., documents=..., embeddings=...)` 一次加入两条记录。三个列表按位置对应；`python` 对应 Python 书和 `[1, 0]`。
4. `query(query_embeddings=[[0.9, 0.1]], n_results=2)` 用一个手写向量找最近的两条。外层列表允许一次提交多个查询。
5. `result["ids"][0]` 等字段中的 `[0]` 取出**第一个查询**的结果；`zip(...)` 把同一条结果的 ID、正文和距离放在一起。

此例使用 Chroma 默认的平方欧氏距离。Python 书到查询向量的距离是 `(1-0.9)² + (0-0.1)² = 0.02`。**距离越小越近**；距离不是百分比，也不是回答正确的概率。

## 4. 看懂现有的 embedding 导出

`content/embedding/basic-pages/` 下有：

- `documents.jsonl`：每行一个 JSON 对象，包含 `id`、`content`、`embedding` 和来源字段。
- `manifest.json`：记录总数、向量维度及 JSONL 文件的 SHA-256 校验值。
- `index-schema.json`：原 Azure AI Search 索引的结构；这个 Chroma 练习不需要它。

本练习检测到 **71 条记录，每条向量 1536 维**。`practice.py` 在写库前检查文件校验值、记录数、ID 唯一性、正文非空和每个向量的长度与数值。这样可以避免把不完整的导出静默写入练习库。

## 5. 把已有向量导入 Chroma

```bash
.venv/bin/python evaluation/chroma/practice.py ingest
.venv/bin/python evaluation/chroma/practice.py inspect
```

第一次命令应报告导入 71 条；第二次应显示 `records: 71` 和三条样例。重复运行 `ingest` 也可以，因为脚本使用 `upsert`：相同 ID 的记录会更新，不会不断增加副本。

打开 [`practice.py`](practice.py) 看导入部分：

- `ROOT`、`SOURCE_DIR`、`DB_DIR` 用脚本自身位置推算路径，因此从其他工作目录调用也能找到数据。
- `read_export()` 读取和验证现有 JSONL。它**不生成 embedding**。
- `PersistentClient(path=...)` 创建磁盘数据库。与 `EphemeralClient` 不同，重新运行脚本仍能读到记录。
- `get_or_create_collection(..., embedding_function=None, configuration={"hnsw": {"space": "cosine"}})` 获取或创建集合，选用 cosine distance。这里的设置针对**新建**集合；已有集合不会因重复运行而改掉距离函数。
- `upsert(ids=..., documents=..., embeddings=..., metadatas=...)` 将导出字段映射到 Chroma。`BATCH_SIZE = 32` 表示分批写入，便于以后处理更大的文件。
- `inspect()` 用 `count()` 看总数，用 `get(limit=3, include=...)` 读取具体记录；`get()` 是按 ID／过滤条件取记录，不是向量相似度检索。

这个脚本传入已有向量，并将 `embedding_function` 设为 `None`，因此 Chroma 不会悄悄下载默认模型或用另一个模型重算向量。

## 6. 完全离线地做一次真实数据检索

```bash
.venv/bin/python evaluation/chroma/practice.py similar christchurch-workshops-1 --limit 3
```

`similar_to_id()` 用 `get(ids=[...], include=["embeddings"])` 取出指定记录的向量，再交给 `query_vector()`。`query_vector()` 调用 `query(query_embeddings=[vector], n_results=...)`，显示 ID、cosine distance、标题、URL 和正文片段。

用某条记录**自己的向量**查询，通常它自己排第一，距离接近 `0`。附近还可能出现其他地区的 workshop 页面。这个步骤不调用 Foundry，不花模型费用，但它只证明本地导入和向量检索通了；它还没有测试自然语言问题的 embedding 效果。

本集合使用 cosine distance，计算方式是 `1 - cosine_similarity`。例如距离 `0.17` 表示方向较接近，但**不能**直接理解成“83% 正确”。是否足够相关，需要结合实际问题和人工检查来判断。

## 7. 用自己的问题检索

这一步才会调用 Microsoft Foundry 的 embedding 部署。先确保仓库根目录的 `.env` 有以下三项，沿用项目已有配置，勿把密钥写进脚本或提交到 Git：

```dotenv
FOUNDRY_OPENAI_BASE_URL=<你的 Foundry OpenAI 兼容 URL>
FOUNDRY_API_KEY=<你的密钥>
FOUNDRY_EMBEDDING_DEPLOYMENT=<生成原始文档向量时使用的部署名>
```

然后运行：

```bash
.venv/bin/python evaluation/chroma/practice.py ask "What AI workshops are available in Christchurch?" --limit 4
```

`ask()` 的顺序是：读取 `.env` → 用 `OpenAI(base_url=..., api_key=...)` 建立客户端 → `client.embeddings.create(model=..., input=question)` 生成**问题向量** → 检查它是否也是 1536 维 → 调用同一个 `query_vector()` 查询。导入阶段不会访问 Foundry；只有 `ask` 会访问网络并可能产生模型费用。这个练习只打印检索结果，不调用聊天模型来生成答案。

`manifest.json` 只记录向量维度，**没有记录原 embedding 模型或部署名**。请确认 `.env` 的部署确实与导出时相同。维度检查只会发现明显错误，不能证明模型相同。如果换了模型，需要重新生成文档向量并重建集合。

## 8. 自己动手修改

1. 在 `chroma_basics.py` 把查询向量改成 `[0.1, 0.9]`，预测哪本书排第一，再运行验证。
2. 在 `practice.py` 的 `inspect()` 里试 `collection.get(where={"source_file": "nelson-workshops.md"}, include=["metadatas"])`，看看 metadata 过滤返回哪些 ID。
3. 换一个已有 ID 运行 `similar`，比较不同页面的距离和来源。
4. 如果已确认 embedding 部署一致，用两三个不同问题运行 `ask`，人工检查前四条是否真的包含答案。

## 常见问题

- **`ModuleNotFoundError: chromadb`**：使用项目的 `.venv/bin/python`，不要误用系统 Python。
- **`Collection is empty`**：先运行 `ingest`。
- **`ID not found`**：先运行 `inspect` 看样例 ID，或检查输入的 ID 是否存在于 JSONL。
- **`Query has ... dimensions`**：问题向量的维度与导出向量不同；检查 embedding 部署。
- **查询结果语义上不相关**：即使维度相同，仍需核实问题和文档是否由同一模型生成，并检查原文内容及切块质量。
- **想重新建练习数据库**：停止使用该练习库后，删除 `evaluation/chroma/db/`，再运行 `ingest`。这只影响本地练习数据。

参考：Chroma 官方文档的 [Python Client](https://docs.trychroma.com/reference/python)、[Collection API](https://docs.trychroma.com/reference/python/collection)、[集合配置与距离函数](https://docs.trychroma.com/docs/collections/configure)；Microsoft 的 [embedding API 示例](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/embeddings?tabs=python-new&view=foundry)。
