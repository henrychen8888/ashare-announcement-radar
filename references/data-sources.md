# 数据源契约与字段含义

两个源均免鉴权、无需 cookie。以下参数与字段索引均为实测确认（2026-10-29 ~ 10-30 校准）。

---

## 一、东方财富公告中心（列表）

```
GET https://np-anotice-stock.eastmoney.com/api/security/ann
```

| 参数 | 值 | 说明 |
|---|---|---|
| `ann_type` | `A` | 沪深 A 股（含北交所），全市场口径 |
| `client_source` | `web` | 固定 |
| `page_size` | `100` | 实测可用上限附近，再大不保证 |
| `page_index` | 1..N | 从 1 开始 |
| `f_node` | `5`/`6`/`3`/`7`/`2` | 分类节点，见下表 |
| `s_node` | `0` | 固定 |

必带请求头：`User-Agent`（普通浏览器 UA 即可）、`Referer: https://data.eastmoney.com/`。

### 五个分类节点

| `f_node` | 分类 | 内容 |
|---|---|---|
| 5 | 重大事项 | 重大合同、中标、增持、业绩预告、股权激励等 |
| 6 | 资产重组 | 收购出售、吸收合并、要约收购、重组进展 |
| 3 | 风险提示 | 退市警示、立案处罚、问询函、异常波动、停牌复牌 |
| 7 | 持股变动 | 增减持、权益变动、质押冻结 |
| 2 | 融资公告 | 增发、配股、可转债、首发、募集资金 |

**必须取并集。** 单看 `f_node=5` 会漏掉全部减持、立案、重组类公告；五节点并集后
按 `art_code` 去重，实测单日 600~800 条（10-27 599 条 / 10-28 636 条 / 10-29 801 条 / 10-30 665 条）。

### 返回结构（关键字段）

```
data.total_hits                 # 总命中
data.list[]                     # 公告条目
  ├── art_code                  # 公告唯一 ID，去重主键，也是正文接口入参
  ├── title                     # 标题，常带「证券简称:」前缀，需剥离
  ├── notice_date               # 2026-10-29
  ├── display_time              # 2026-10-29 20:31:05
  ├── columns[].column_name     # 结构化分类，打标的主依据（比标题可靠）
  ├── codes[0].stock_code       # 6 位代码
  ├── codes[0].short_name       # 证券简称
  └── attach_url / attach_url_web  # PDF 地址
```

`columns[].column_name` 是本技能最重要的字段：它是交易所披露时打的分类标签，
形如「重大合同」「股东/实际控制人股份减持」「业绩预增」。用它判方向远比猜标题准。

### 分页策略

`fetch.py` 的 `MAX_PAGES = 45`。抓当日时按 `notice_date` 倒序翻页，
**翻到日期早于目标日即停**，不会白翻。补抓历史日期时才可能吃满上限。

---

## 二、东方财富公告正文

```
GET https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code=<ART_CODE>&client_source=web&page_index=1
```

返回 `data.notice_content`，是带换行的纯文本。**只对 S 级抓正文**：
每条一次请求，全量抓会把 9 秒变成几分钟，且对看板价值没有增量。

正文前 200 字通常全是噪音：证券代码、公告编号、公司全名、重复标题、免责声明模板。
`fetch_content()` 会剔除这些，并优先截取「重要内容提示」之后的实质内容，
再按句号/分号切在 260 字以内。新增剔除规则前先看几条真实正文，别照标题猜。

---

## 三、腾讯财经行情

```
GET https://qt.gtimg.cn/q=sh601600,sz000858,bj920306
```

- 编码 **GBK**，非 UTF-8。
- 单次批量约 60 只，超过需分批。
- 代码前缀规则：`6`/`9`/`11` → `sh`；`920`/`8`/`4` 开头 → `bj`；其余 `sz`。

**前缀判断顺序有坑：`920` 必须放在「9 开头 = 沪市」之前判断。**
反了会把北交所股票拼成 `sh920xxx`，接口不报错、静默返回空，表现为「这些票没有行情」。

### 字段索引（`~` 分隔）

| idx | 含义 | idx | 含义 |
|---|---|---|---|
| 1 | 名称 | 37 | 成交额（万元） |
| 2 | 代码 | 38 | 换手率（%） |
| 3 | 现价 | 44 | 流通市值（亿） |
| 4 | 昨收 | 45 | 总市值（亿） |
| 32 | 涨跌幅（%） | **47** | **涨停价** |
| — | — | **48** | **跌停价** |

idx 47/48 是这个源的核心价值：直接给出交易所涨停价，涨停判定不用自己推。

### 为什么不用东方财富 push2 行情

`push2.eastmoney.com/api/qt/ulist.np/get` 在实测中稳定返回空 `data`（HTTP 200，
无报错），换 host、换 UA、加减 `fltt` 都无效。**静默失败**最难排查，故整体改用腾讯源。

---

## 四、接口自检

怀疑数据源变更时，按顺序验：

```bash
# 1. 列表接口是否还活着（应返回 total_hits > 0 且 list 非空）
curl -s "https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=3&page_index=1&ann_type=A&client_source=web&f_node=5&s_node=0" \
  -H "User-Agent: Mozilla/5.0" -H "Referer: https://data.eastmoney.com/" | head -c 400

# 2. 五个节点分别有多少条（任一为 0 说明该节点失效，需换 f_node）
for fn in 5 6 3 7 2; do
  echo -n "f_node=$fn: "
  curl -s "https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=1&page_index=1&ann_type=A&client_source=web&f_node=$fn&s_node=0" \
    -H "User-Agent: Mozilla/5.0" -H "Referer: https://data.eastmoney.com/" \
    | python -c "import sys,json;print(json.load(sys.stdin).get('data',{}).get('total_hits'))"
done

# 3. 行情接口（应看到 GBK 中文名，且字段数 >= 50）
curl -s "https://qt.gtimg.cn/q=sh601600" -H "User-Agent: Mozilla/5.0" -H "Referer: https://finance.qq.com/" | iconv -f GBK -t UTF-8 | head -c 300
```

三条全通而抓取仍异常，问题在本仓库的解析逻辑，不在数据源。
