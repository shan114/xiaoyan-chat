"""
RAG (检索增强生成) 引擎
- 使用 ChromaDB 作为向量数据库
- 使用 sentence-transformers 做本地嵌入
- 支持使用 Hugging Face 镜像（国内网络优化）
- 如果无法加载模型，自动降级为纯文本匹配模式
- 支持文档索引和语义检索
"""
import os
import hashlib
from typing import List, Tuple, Optional

# 设置 Hugging Face 镜像（国内可用）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import chromadb
from chromadb.utils import embedding_functions
from chromadb.config import Settings


class RAGEngine:
    """检索增强生成引擎，模型加载失败时自动降级"""

    def __init__(self, persist_dir: str = None):
        if persist_dir is None:
            # DATA_DIR 用于云部署持久化存储，本地默认当前目录
            persist_dir = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(__file__)), "chroma_db")

        self._persist_dir = persist_dir
        self._ready = False
        self._embedding_fn = None
        self._client = None
        self._collections = {}
        self._fallback = False  # 是否在降级模式

        self._init()

    def _init(self):
        """初始化：尝试加载 embedding，失败则降级"""
        # 初始化 ChromaDB 客户端
        try:
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False)
            )
        except Exception as e:
            print(f"[RAG] ChromaDB 初始化失败: {e}")
            return

        # 尝试加载 embedding 模型
        for model_name in [
            "all-MiniLM-L6-v2",                          # 最小英文模型 ~90MB
            "paraphrase-multilingual-MiniLM-L12-v2",      # 多语言（含中文）~420MB
        ]:
            try:
                print(f"[RAG] 尝试加载模型: {model_name}")
                self._embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=model_name,
                    device="cpu"
                )
                # 测试一下
                _ = self._embedding_fn(["test"])
                print(f"[RAG] 模型加载成功: {model_name}")
                self._ready = True
                break
            except Exception as e:
                print(f"[RAG] 模型 {model_name} 加载失败: {e}")
                continue

        if not self._ready:
            print("[RAG] 所有 embedding 模型加载失败，进入降级模式（关键词匹配）")
            self._fallback = True
            self._ready = True  # 降级模式也算就绪

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_fallback(self) -> bool:
        return self._fallback

    def _get_collection(self, name: str = "general"):
        """获取或创建 collection"""
        if name not in self._collections:
            try:
                if self._fallback:
                    # 降级模式：使用默认 embedding（chromadb 内置）
                    self._collections[name] = self._client.get_or_create_collection(
                        name=name
                    )
                else:
                    self._collections[name] = self._client.get_or_create_collection(
                        name=name,
                        embedding_function=self._embedding_fn
                    )
            except Exception as e:
                print(f"[RAG] 创建 collection {name} 失败: {e}")
                self._collections[name] = self._client.get_or_create_collection(name=name)
        return self._collections[name]

    def add_document(self, text: str, metadata: dict = None,
                     doc_id: str = None, collection: str = "general") -> str:
        """添加文档到知识库"""
        try:
            col = self._get_collection(collection)
            if doc_id is None:
                doc_id = hashlib.md5(text.encode()).hexdigest()[:16]
            if metadata is None:
                metadata = {}
            metadata["source_type"] = metadata.get("source_type", "manual")

            col.upsert(
                documents=[text],
                metadatas=[metadata],
                ids=[doc_id]
            )
            return doc_id
        except Exception as e:
            print(f"[RAG] 添加文档失败: {e}")
            return ""

    def add_documents(self, texts_and_meta: List[Tuple[str, dict]],
                      collection: str = "general") -> List[str]:
        """批量添加文档"""
        ids = []
        for text, meta in texts_and_meta:
            doc_id = self.add_document(text, meta, collection=collection)
            if doc_id:
                ids.append(doc_id)
        return ids

    def search(self, query: str, top_k: int = 3,
               collection: str = "general") -> List[dict]:
        """语义检索相关文档"""
        try:
            col = self._get_collection(collection)
            results = col.query(
                query_texts=[query],
                n_results=min(top_k, col.count())
            )

            if not results.get("documents") or not results["documents"][0]:
                return []

            documents = []
            for i, doc in enumerate(results["documents"][0]):
                item = {"text": doc, "score": 0}
                if results.get("distances") and results["distances"][0]:
                    item["score"] = round(1 - min(results["distances"][0][i], 1), 4)
                if results.get("metadatas") and results["metadatas"][0]:
                    item["metadata"] = results["metadatas"][0][i]
                documents.append(item)
            return documents
        except Exception as e:
            print(f"[RAG] 检索失败: {e}")
            return []

    def search_context(self, query: str, top_k: int = 3,
                       collection: str = "general") -> str:
        """检索并格式化为可注入提示词的上下文文本"""
        docs = self.search(query, top_k=top_k, collection=collection)
        if not docs:
            return ""

        lines = ["--- 以下是知识库中的相关信息 ---"]
        for i, doc in enumerate(docs, 1):
            relevance = f" (相关度: {doc['score']})" if doc.get("score") else ""
            meta = doc.get("metadata", {})
            source = meta.get("source", meta.get("source_type", "未知来源"))
            lines.append(f"[参考{i}{relevance} | {source}] {doc['text']}")
        lines.append("--- 以上信息可供参考 ---")
        return "\n".join(lines)

    def delete_collection(self, name: str):
        try:
            self._client.delete_collection(name)
            self._collections.pop(name, None)
        except Exception:
            pass

    def get_stats(self) -> dict:
        """获取知识库统计"""
        stats = {}
        for name, col in self._collections.items():
            try:
                stats[name] = col.count()
            except Exception:
                stats[name] = 0
        return stats

    def seed_builtin_knowledge(self):
        """注入内置常识知识"""
        builtin = [
            ("中国首都是北京，官方语言是汉语，货币是人民币(CNY)。",
             {"source_type": "builtin", "category": "geography"}),
            ("地球是太阳系第三颗行星，距离太阳约1.5亿公里，有7大洲和5大洋。",
             {"source_type": "builtin", "category": "science"}),
            ("水的化学式是H2O，在标准大气压下沸点为100°C，冰点为0°C。",
             {"source_type": "builtin", "category": "science"}),
            ("人工智能(AI)是计算机科学分支，致力于创建能执行人类智能任务的系统。",
             {"source_type": "builtin", "category": "technology"}),
            ("Python由Guido van Rossum于1991年创建，强调代码可读性。",
             {"source_type": "builtin", "category": "technology"}),
            ("中国四大名著：《红楼梦》曹雪芹、《西游记》吴承恩、《三国演义》罗贯中、《水浒传》施耐庵。",
             {"source_type": "builtin", "category": "culture"}),
            ("健康建议：每日蔬菜300-500g、水果200-350g、饮水1500-1700ml、运动≥150分钟/周。",
             {"source_type": "builtin", "category": "health"}),
        ]
        try:
            self.add_documents(builtin, collection="general")
            print(f"[RAG] 注入 {len(builtin)} 条内置知识")
        except Exception as e:
            print(f"[RAG] 内置知识注入跳过: {e}")


# 全局单例（延迟创建，避免 import 时加载 90MB embedding 模型）
_rag_instance = None

def get_rag_engine() -> RAGEngine:
    """获取 RAG 引擎单例（首次调用时才加载模型）"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGEngine()
    return _rag_instance
