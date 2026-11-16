import chromadb

# 内存数据库：程序退出后数据消失。这里手写二维向量，先不涉及模型或 API。
db = chromadb.EphemeralClient()
books = db.create_collection("books", embedding_function=None)

# 每个 ID、文本和向量一一对应。没有 embedding function 时必须自己提供向量。
books.add(
    ids=["python", "cooking"],
    documents=["一本 Python 编程书", "一本烹饪食谱"],
    embeddings=[[1.0, 0.0], [0.0, 1.0]],
)

result = books.query(
    query_embeddings=[[0.9, 0.1]],
    n_results=2,
    include=["documents", "distances"],
)

# query 是批量接口，所以外层 [0] 表示第一个查询的结果。
for name, text, distance in zip(
    result["ids"][0],
    result["documents"][0],
    result["distances"][0],
):
    print(name, text, round(distance, 3))
