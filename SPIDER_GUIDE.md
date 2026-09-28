# 写爬虫源方法论（TVBox / FongMi type=3）
> 剧踪、金牌、zip0、追影、libvio、fcjav… 十几套源跑通后沉淀下来的通用打法。
> 铁律都标了 ★，出问题八成是踩了带 ★ 的条。

---

## 第一步：分析先行（70% 时间抓包，30% 时间写码）

**不要拿到网站就开始写代码。** 先摸清 4 件事，写的时候就是纯翻译。

### 1.1 先探技术栈（决定走哪套套路）

| 探测 | 看什么 | 结论 → 走哪套 |
|---|---|---|
| `curl -sL 域名/api.php/provide/vod/?ac=list` | 返回 JSON 列表 | **苹果CMS 采集接口**，最省事，直接吃 JSON |
| 同上 | 返回首页 HTML / 301 | 采集接口已关 → 走 **HTML 直抓** |
| 页面 URL | `/voddetail/` `/vodshow/` `/vodplay/` | 苹果CMS（v8/v10/魔改）规律直接套 |
| 页面 URL | `/detail/` `/type/` `/play/` | libvio / 网飞TV 自写模板 |
| 源码 | `<script id="__NEXT_DATA__">` | Next.js → **一条正则抠全站 JSON** |
| 源码 | `<script id="__NUXT_DATA__">` / `window.__NUXT__` | Nuxt，同上 |
| 源码 | `htmx` / `HX-Request` | 现代 HTMX 站，要伪请求头 |
| 页面空白但浏览器能开 | 请求头有 `sign`/`token`/`deviceId` | **API 签名站**，逆签名（第五章） |

### 1.2 摸 5 个 URL 规律（curl 实测，别猜）

```
① 列表   /vodtype/1/          /vodshow/1-----------/
② 分页   必须实测第2页！看分页链接拼法，数清位次
③ 详情   /voddetail/60937/
④ 播放   /vodplay/60937-1-1/   ← 详情页里扒出来的真链接
⑤ 搜索   /vodsearch/关键词----------1---/
```

★ **分页必须实测第 2 页**。剧踪站是 12 段位：
`{tid}-{地区}-{排序}--------{页码}------{年份}`（1=地区 2=排序 8=页码 11=年份）。
位次数错 = 所有分类内容一样 / 翻页空白。

### 1.3 摸播放地址（最容易翻车）

- 详情页**常常没有**播放地址，真地址在 `/vodplay/` 播放页
- 播放页搜 `var player_data=` → `url` 就是答案：
  - 明文 `https://xxx/index.m3u8` → 直接用
  - `encrypt:1/2` → `unescape` 或 `base64 + unescape`
  - 自定义前缀（`juzongx-` 这种）→ **走第五章逆向流程**
- 有 iframe → 抠 iframe 的 src 再进去找

★ **静态读 JS 找不到时，直接用 playwright 开真页面抓网络请求**，
比读混淆代码快 10 倍（剧踪站密文就是这么破的）。

### 1.4 摸反爬

- 搜 `403` / `开始验证` / `cf_clearance` / `verify`
- ★ **WAF 403 通用解法**：首访 403 会下 cookie，**同一 cookie 会话重试一次就 200**
- 浏览器能开、curl 抓不到 → 不是站挂了，是缺 cookie / UA / 签名

### 1.5 摸图床

- 相对路径要拼域名（拼错 = 全站无海报）
- ★ **拼接域名以「实测能用的版本」为准**，我这边探活失败不能作为否决依据

---

## 第二步：加载层铁律（不守 = 整源白屏）

这 5 条是空白事故复盘出来的，**每条都真实炸过**：

```python
# -*- coding: utf-8 -*-
try:
    from base.spider import Spider as BaseSpider   # ★ FongMi/Chaquopy 认这个名字
except Exception:
    class BaseSpider(object):
        def __init__(self): pass

class Spider(BaseSpider):
    def __init__(self):                    # ★ 必须显式写
        super(Spider, self).__init__()     # ★ 必须先调父类
        self.host = "https://..."

    def getDependence(self):               # ★ 必须 return []
        return []                          #   写["requests"]=壳子去装依赖=失败=纯白屏

    def localProxy(self, param=None):      # ★ 参数给默认值，返回四元组
        return [404, "text/plain", b"", {"Content-Type": "text/plain"}]
```

**交付前自查**：
- [ ] `from base.spider import Spider` 有 try/except 双模式
- [ ] `getDependence()` 返回 `[]`
- [ ] 显式 `__init__` 且先 `super().__init__()`
- [ ] `localProxy(self, param=None)` 返回 **4 元组**，body 是 `bytes`
- [ ] 有 `getName()` / `isVideoFormat()` / `manualVideoCheck()` / `init(extend)`
- [ ] requests 是文件头 `try import` 自兜底，不进 getDependence

---

## 第三步：六接口契约（一次成型，不返工）

### 3.1 方法签名

```python
init(extend)                              # extend 兼容 空串/URL/dict/JSON串 四态
homeContent(filter=False)                 # class + filters + list
homeVideoContent()                        # {list:[...]}  部分壳无条件调用，必须有
categoryContent(tid, pg, filter, extend)  # list/page/pagecount/limit/total
searchContent(key, quick, pg="1")
detailContent(ids)                        # vod_play_from / vod_play_url
playerContent(flag, id, vipFlags)         # {parse, url, header}
```

### 3.2 返回结构铁律 ★

```python
{"page":1, "pagecount":10, "limit":36, "total":360, "list":[
    {"vod_id":"60937", "vod_name":"早春晴朗",
     "vod_pic":"https://...", "vod_remarks":"更新至18集"}
]}
```

- **无数据也返回全套字段**（pagecount 至少 1），缺字段 = 壳子崩或空白
- `page` 必须 `int`
- ★ 搜索/分类没数据也要回完整结构，绝不能 `return None`

### 3.3 分隔符铁律 ★（错了 = 选集塌陷 / 线路串台）

```
线路之间   $$$      vod_play_from 和 vod_play_url 段数必须严格相等
线路内     #        集与集
每集       名称$地址  集名不重复，不含 $ 和 #
```

```python
vod_play_from = "线路A$$$线路B"
vod_play_url  = "第01集$xxx@@1@@1#第02集$xxx@@1@@2$$$第01集$xxx@@2@@1"
```

### 3.4 播放 id 编码 ★

```python
full_id = "%s@@%s@@%s" % (vid, sid, nid)   # 站内多线路
full_id = "key@vid"                         # 多源聚合，否则 A源id 在B源是别的片
```
`playerContent` 里 split 回来，**同时兼容旧格式**（缓存里的旧 id 才不失效）。

### 3.5 playerContent 返回 ★

```python
return {"parse": 0, "url": 直链, "header": {"User-Agent": UA}}
```
- `parse:0` = 给直链 / 回页面交 App 嗅探；`parse:1` = 交壳子解析接口
- ★ header **绝对不能** `json.dumps()` 成字符串，必须 dict
- ★ 拿不到地址**返回空也不崩**

---

## 第四步：网络单一入口 + 解析独立

### 4.1 统一 `_get()`

```python
def _get(self, url, referer=None, timeout=12, retry=2):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer: headers["Referer"] = referer
    s = self._sess()                      # requests.Session 连接池
    for i in range(retry + 1):
        r = s.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200: return r.text
        if r.status_code not in (403, 429, 503): break   # ★ 403 不 break，重试过 WAF
        time.sleep(0.4 * (i + 1))
    return ""                             # ★ 失败回空串，永不抛
```

### 4.2 取流头铁律 ★

| 场景 | 头 |
|---|---|
| 抓站内页面 | UA + `Referer: 站点首页` |
| **播直链（CDN）** | **只带 UA，绝不带 Referer**（douyinvod 带 Referer 必 403） |
| 播放器页有 `<meta name=referrer no-referrer>` | 就是告诉你别带 Referer |

302 跳转交给播放器跟随，源端不用管。

### 4.3 封面多字段取图 ★

```python
for attr in ("data-original", "data-src", "data-lazy-src", "data-poster", "src"):
    ...
# 黑名单宁窄勿宽，只留已验证的词：
# loading / blank / placeholder / 1x1 / transparent / .svg
# ★ 别加 lazy / default，会误杀真图
```

### 4.4 解析独立

卡片 / 详情 / 线路各一个函数，正则预编译，每个都 `try/except` 兜底。

---

## 第五步：播放四层兜底 + 解密逆向

### 5.1 四层兜底（永远有第 4 层）

```
① 抠直链（player_data.url / 密文解密 / video标签 / iframe 二次）
② 多正则找 m3u8|mp4|flv（JS 转义 \/ 还原、协议相对 // 补 https:）
③ 拿不到 → 返回播放页 URL + parse:0，交 App 内建嗅探
④ 再不行 → parse:1 给有解析接口的壳
```

### 5.2 parse0 / parse1 选型表 ★

| 场景 | 用法 |
|---|---|
| 有真直链（m3u8/mp4） | `parse:0` + 直链 ✅ |
| 普通 MacCMS 播放页 | 有解析接口的壳 → `parse:1` 最稳；无 → `parse:0` 回播放页嗅探 |
| 站内播放页 | ★ **绝不能** `parse:1` + 站内播放页（实测转圈） |
| 外链（youku/qq） | `parse:0` 回原页交 App 嗅探 |

### 5.3 加密/签名破解分档（由易到难）

| 档 | 特征 | 手段 |
|---|---|---|
| 简单 | `encrypt:1/2`、`%u`、base64 | `unescape` / `base64decode` |
| 中等 | AES 固定 key/iv | `Crypto.Cipher`（装不上纯 Python 手写） |
| 动态 key | `reverse(base64(...))` 拼 key | 看 JS 拼接逻辑逆 |
| **API 签名** | 头里有 `sign`/`t`/`deviceId` | ★ **playwright 抓完整请求 → 定位签名函数 → 抠 key → 本地验签 → 纯 requests 直连**（金牌站 30 分钟搞定） |
| 密文自定义前缀 | `juzongx-xxx` | ★ 剧踪三步：<br>1. 静态读 JS 找不到 → playwright 开真播放页抓 network<br>2. 发现 iframe 指向中转站 → 再开它<br>3. 抓到 `api.php?action=...&token=...` 成功响应 → **纯 curl 复现** |

★ **通用心法**：别硬读混淆 JS，**开浏览器看它最后请求了什么**，逆着过来就是解法。

### 5.4 反爬等级应对

| 等级 | 特征 | 手段 |
|---|---|---|
| 0-1 | 无 / 只查 UA | UA + Session |
| 2 | 403 挑战 / cookie | ★ 同会话重试一次 |
| 3 | Cloudflare | 换镜像域名 / 手动过一次存 cookie（别硬刚） |
| 4 | reCAPTCHA / Turnstile | **不硬解、不打码**，换域、换 API、手动过一次存 cookie |

---

## 第六步：容灾 + 性能

```python
# ① 域名池：镜像天天换，主域失败自动切；extend 填新域名，换站只改一处
DOMAINS = ["https://www.a.com", "https://a2.com"]

# ② TTL 缓存：分类 5min / 搜索 3min / 详情 10min，超量 clear

# ③ 短超时快速重试：列表 6s、接口 4s、重试 2 次

# ④ 连接池：HTTPAdapter(pool_connections=8, pool_maxsize=16)

# ⑤ 空页刹车 ★（防无限翻页）
pagecount = pg if not lst else (尾页数字 or pg + 1)
# 伪分页 999/9999 直接判失败

# ⑥ 分类兜底：动态抓导航失败 → 写死 1电影 2电视剧 3综艺 4动漫
```

---

## 第七步：自测 + 交付铁律 ★

### 7.1 交付前必须自己测完（四件套）

```bash
python3 -m py_compile 你的文件.py     # 1. 秒查语法
python3 自测脚本.py                   # 2. 五接口实测（真网络）
# 3. 关键正则拿真实页面源码当样本跑一遍
# 4. curl 带 UA/Referer 实抓核对字段
```

**自测报告表随成品一起发**，测不到的明确标「待你点一下」。
> 没自测就交付 = 连续返工三版（血泪教训）。

### 7.2 交付三件套

1. **新文件名**（如 `xxx2.py`）→ 强制重编译，防旧缓存
2. 保姆级 3 步：存文件 → **删旧源重导** → **强杀 App 重开**
3. 附自测报告表（✅已验证 / ❌验不了分列）

### 7.3 报现象秒修（你只需说这一句）

| 你说 | 我定位 |
|---|---|
| 首页/分类**全空白** | 文件级问题：语法错/截断/旧缓存 → 查完整性+换文件名+强杀 |
| 分类有但**列表空** | 选择器 / 分页位次 / URL 拼错 |
| **只有某条线路**不响 | 逻辑级问题，只改那个分支 |
| 搜索**转圈** | 反爬 / WAF / 编码 |
| 能播但**无海报** | 纯图片链路（拼接域 → 属性 → 防盗链） |

★ **两种现象绝不混修**：把逻辑问题当文件问题整锅换，会制造出文件问题。

---

## 附一：修改铁律（避免越修越坏）

1. ★ **验证过好用的版本 = 资产，只改失败的那一个点，绝不整份重发**
2. 改前先问「上一版哪几条是好的」→ 列差异 → 只替换那一处
3. 同一问题连续 2 版失败 → **停止猜**，要真实样本（源码/真实URL），拿到一次焊死
4. 长代码交付附完整性检查点（开头 `# -*- coding: utf-8 -*-`、结尾配平）
5. 黑名单宁窄勿宽，只写已验证的词

## 附二：精华 / 糟粕对照（拿到新范例照此拆）

| 类型 | 处理 |
|---|---|
| **精华**（新套路/新坑位） | 沉淀进本文件对应章节，下次直接套 |
| **老熟脸**（苹果CMS域池、四层播放兜底…） | 已有，不重复 |
| **糟粕**（残缺拼接、未定义变量、伪分页、`json.dumps(header)`） | 当场拉黑，只取想法不抄码 |

> 收到新范例时说一句「想重点学啥」（缓存/签名/多线路/域名池），我直奔那点拆。
