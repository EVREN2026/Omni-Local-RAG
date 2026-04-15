"""
Shared category keywords and heuristic classification logic.

Used by both inference_worker (routing) and json_db_sync (auto-categorization
at import time) to avoid circular imports.
"""

_TECH_KEYWORDS = {
    "api", "sdk", "python", "java", "cpp", "安装", "部署", "代码", "参数", "模型", "llm", "cuda",
    "故障", "调试", "配置", "接口", "版本", "依赖", "command", "linux", "windows",
}
_BUSINESS_KEYWORDS = {
    "sop", "流程", "制度", "审批", "业务", "运营", "客服", "销售", "采购", "培训", "规范", "手册",
}
_FOLLOWUP_MARKERS = {"它", "这个", "那个", "其", "这", "那", "上面", "前面", "刚才", "上一条"}


def contains_any(text: str, keywords: set) -> bool:
    content = (text or "").lower()
    return any(k in content for k in keywords)


def heuristic_category(text: str) -> str:
    if contains_any(text, _TECH_KEYWORDS):
        return "tech_manual"
    if contains_any(text, _BUSINESS_KEYWORDS):
        return "business_sop"
    return "general"
