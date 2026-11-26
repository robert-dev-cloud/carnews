"""
判斷一則政府公告跟「車主」有沒有關係，並分類。

設計原則：寧可漏掉也不要誤報。
  - 標題命中「強訊號」就算相關
  - 只在內文命中的，要同時有「車輛語境」才算
  - 明顯是鐵路、航空、船舶、工廠的排除
"""
import re

# 主題 -> 關鍵字（命中標題權重高）
TOPICS = {
    "噪音": ["噪音車", "聲音照相", "機動車輛噪音", "噪音管制", "排氣管", "使用中機動車輛噪音", "高噪音"],
    "排氣與空污": ["排氣檢驗", "機車定檢", "車輛空氣污染物", "交通工具空氣污染物", "移動污染源", "老舊機車", "老舊車輛",
              "柴油車", "空氣品質維護區", "汰舊換新", "烏賊車", "排放標準"],
    "罰則與交通規則": ["道路交通管理處罰條例", "道路交通安全規則", "違反道路交通管理事件統一裁罰基準", "交通違規", "記點",
                "罰鍰", "酒駕", "酒後駕車", "行人優先", "路口不停讓", "超速", "區間測速", "科技執法", "測速",
                "吊扣", "吊銷", "違規停車", "檢舉"],
    "驗車與改裝": ["車輛檢驗", "定期檢驗", "驗車", "變更登記", "車輛安全檢測基準", "車輛型式安全審驗", "改裝", "車輛召回", "召回改正"],
    "駕照與考照": ["駕駛執照", "駕照", "考照", "道路駕駛考驗", "高齡駕駛", "換照", "駕訓"],
    "稅費與補助": ["使用牌照稅", "汽車燃料使用費", "燃料費", "貨物稅", "強制汽車責任保險", "強制險", "通行費", "停車費", "購車補助", "電動機車補助"],
    "電動車": ["電動車", "電動機車", "電動汽車", "充電樁", "充電站", "電動輔助自行車", "微型電動二輪車"],
    "國道與路況": ["國道", "高速公路", "匝道", "連續假期", "疏運", "高乘載", "封閉施工", "路段封閉"],
}

# 內文命中時必須同時出現的車輛語境
VEHICLE_CONTEXT = ["汽車", "機車", "車輛", "駕駛人", "車主", "機動車", "小客車", "大型重型", "貨車", "道路交通"]

# 標題出現這些而且沒有車輛語境，就排除
EXCLUDE = ["鐵路", "臺鐵", "台鐵", "高鐵", "捷運", "航空", "航空器", "民航", "機場", "船舶", "航港", "商港", "漁船",
           "郵政", "觀光", "氣象", "電信", "固定污染源", "工廠", "營建工程", "招標", "徵才", "得標", "人事"]

CAR_WORDS = ["汽車", "小客車", "小型車", "貨車", "大客車", "柴油車"]
MOTO_WORDS = ["機車", "機器腳踏車", "大型重型", "普通重型", "輕型", "電動機車", "微型電動二輪車"]

# 對象不是一般車主的：營業車、製造商與進口商。保留但另外標示，App 預設不推播
COMMERCIAL = ["計程車", "汽車運輸業", "客運", "貨運", "遊覽車", "職業駕駛", "租賃業"]
INDUSTRY = ["應施檢驗", "型式安全審驗", "審查費", "證書費", "審驗合格證明", "商品檢驗", "製造廠", "進口商"]

TITLE_WEIGHT = 3
BODY_WEIGHT = 1
THRESHOLD = 3


def _count(words, text):
    return [w for w in words if w in text]


def classify(title: str, summary: str = "", text: str = "", keywords=None):
    keywords = keywords or []
    head = f"{title} {' '.join(keywords)}"
    body = f"{summary} {text}"
    whole = f"{head} {body}"

    has_vehicle_ctx = bool(_count(VEHICLE_CONTEXT, whole))
    excluded = _count(EXCLUDE, title)

    score, topics, matched = 0, [], []
    for topic, words in TOPICS.items():
        in_head = _count(words, head)
        in_body = [w for w in _count(words, body) if w not in in_head]
        s = len(in_head) * TITLE_WEIGHT + (min(len(in_body), 3) * BODY_WEIGHT if has_vehicle_ctx else 0)
        if in_head or (in_body and has_vehicle_ctx and len(in_body) >= 2):
            topics.append((s, topic))
            matched += in_head + in_body
        score += s

    # 「噪音」「排放標準」「罰鍰」單獨出現太泛，要求有車輛語境
    if not has_vehicle_ctx:
        score = 0
    # 標題明顯屬於其他領域：只有在標題同時有車輛字眼時才保留
    if excluded and not _count(CAR_WORDS + MOTO_WORDS + ["車輛", "道路交通", "國道"], title):
        score = 0

    relevant = score >= THRESHOLD and bool(topics)
    vehicles = []
    if _count(CAR_WORDS, whole):
        vehicles.append("汽車")
    if _count(MOTO_WORDS, whole):
        vehicles.append("機車")
    if relevant and not vehicles:
        vehicles = ["汽車", "機車"]

    if _count(COMMERCIAL, title):
        audience = "營業車"
    elif _count(INDUSTRY, title):
        audience = "車廠與業者"
    else:
        audience = "一般車主"

    return {
        "relevant": relevant,
        "audience": audience if relevant else "",
        "score": score,
        "topics": [t for _, t in sorted(topics, reverse=True)][:3] if relevant else [],
        "vehicles": vehicles if relevant else [],
        "matched": sorted(set(matched)) if relevant else [],
    }
