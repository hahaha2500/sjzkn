# 默影视 / WebHTV 壳子速查索引（源码级）

- 仓库：https://github.com/Silent1566/webhtv （fork of webhtv/webhtv）
- 本地：`/workspace/webhtv`（HEAD d187f6a 2026-09-24，release v5.6.0-beta-202609220102）
- 用途：写/修 TVBox Python 源（type=3）时对齐壳的加载层与回调契约

## 1. 关键文件

| 文件 | 作用 |
|---|---|
| `chaquo/src/main/python/app.py` | Python 侧总入口，壳每个接口都经它转发 |
| `chaquo/src/main/python/base/spider.py` | 基类 `Spider`（ABCMeta + **单例**），含 fetch/post/html/getProxyUrl/getCache |
| `chaquo/src/main/java/com/fongmi/chaquo/Loader.java` | 下载 py 脚本 → SourceFileLoader 加载 → `.Spider()` |
| `chaquo/src/main/java/com/fongmi/chaquo/Spider.java` | Java↔Py 桥，六接口 + localProxy 的真契约 |
| `app/.../api/loader/BaseLoader.java` | **localProxy 路由总闸** |
| `app/.../api/loader/PyLoader.java` | py 源实例缓存 + 代理回调 |
| `app/.../server/Server.java` | 本地服务，端口 9978~9999 |
| `catvod/.../crawler/Proxy.java` | `Proxy.getUrl(local)` → `http://127.0.0.1:<port>/proxy` |
| `app/.../server/process/*.java` | NanoHTTPD 路由表 |

## 2. localProxy 完整路由（BaseLoader.java:79-84）

```java
if (params.containsKey("siteKey")) return getSpider(params.get("siteKey")).proxy(params);
if ("js".equals(params.get("do")))     return jsLoader.proxy(params);
if ("py".equals(params.get("do")))     return pyLoader.proxy(params);
return jarLoader.proxy(params);   // ← 兜底，py 源走这里 = 返回 null = Invalid proxy response
```

**结论（已验证）**
1. py 源的本地代理地址必须带 **`do=py`**，或带 **`siteKey`**。
2. ⚠️**新坑**：`PyLoader.proxy` 开头 `if (recent == null) return null;`
   `recent` 由 `Site.recent()` → `BaseLoader.setRecent()` 设置。
   → **只写 `?do=py` 而壳没先 `recent()` 过 → 直接返回 null → Invalid proxy response。**
   → **保险写法：地址同时带 `siteKey=<源key>`**，走第一分支，不依赖 recent。
3. `?do=local` 不是合法值（Local 路由是 `/file|/upload|/newFolder|/delFolder|/delFile`）。

## 3. localProxy 返回值真契约（chaquo/Spider.java:89-97）

```java
List<PyObject> list = app.callAttr("localProxy", obj, gson.toJson(params)).asList();
boolean base64 = list.size() > 4 && list.get(4).toInt() == 1;   // 第5位=1 → body 是 base64
boolean header = list.size() > 3 && list.get(3) != null;         // 第4位非空 → 读 header
result[0]=list.get(0).toInt();      // HTTP status
result[1]=list.get(1).toString();   // mime
result[2]=getStream(list.get(2), base64);  // body
result[3]=header?getHeader(list.get(3)):null;
```

- **list 至少要 3 个元素**（0/1/2 直接下标取，少了会 IndexOutOfBounds）→ 三元组 `[200, mime, bytes]` 合法 ✅
- **第 4 位 header 必须是 dict**（`getHeader` 用 `obj.asMap()`，字符串会抛异常被吞成 null）
- **第 5 位 = 1 → body 按 base64 解码**（`Util.decode`），可用来返回二进制（如合法 1x1 PNG）免字节流
- `body` 支持 bytes（直接用）/ str（按编码取）
- 形如 `{base}?do=py&url=<base64 url>&type=img`

## 4. 端口与地址

- `Server.start()`：`for (int i = 9978; i < 9999; i++)` 取首个空闲口 → **绝不能写死端口**
- 基类已提供 `self.getProxyUrl(local=True)` → `http://127.0.0.1:<port>/proxy?do=py`
  （裸 `class Spider:` 无此方法 → 需 `from com.github.catvod import Proxy; Proxy.getUrl(True)` 或扫端口）
- ⚠️ base64 参数必须 `quote(safe='')`，`+` 不转会被 query 解析成空格

## 5. NanoHTTPD 路由表（server/process）

| 路由 | 类 |
|---|---|
| `/action` | Action |
| `/cache` | Cache ← **壳提供的持久缓存** |
| `/debug/{diag,logs,mpd,stream,clear,enable,disable}` | DebugLogs ← **调试日志** |
| `/drivecheck` `/pan/check` | DriveCheck |
| `/file` `/upload` `/newFolder` `/delFolder` `/delFile` | Local |
| `/m3u8` | M3u8 |
| `/manage` | Manage |
| `/media` | Media |
| `/parse` | Parse |
| `/playback*` | PlaybackProgressApi / PlaybackRecordApi |
| `/proxy` | Proxy |
| `/webResource` | WebResourceGateway |

## 6. 基类能力（base/spider.py）— 白捡的

```python
fetch(url, params,cookies,headers,timeout=5,verify,stream,allow_redirects)  # requests.get，rsp.encoding 强制 utf-8
post(...)                        # 同上 requests.post
html(content) -> lxml etree.HTML
str2json / json2str
getProxyUrl(local=True)          # 拿真端口代理地址
getCache(key) / setCache(key,value) / delCache(key)   # 走 /cache?do=get|set|del，跨重启持久化
log(msg)                         # dict/list 自动 json
loadSpider(name) / loadModule(name)   # 加载缓存目录 py/name.py
regStr / removeHtmlTags / cleanText
```

### ★ `getCache/setCache` 是解决「详情页自身没封面」的正解
- dict/list 会被 json 序列化；dict 里可放 `expiresAt`（Unix 秒）自动过期
- 存在 App 沙盒里，**比模块级字典更强**（壳重启/清缓存后仍在）
- 用法：`setCache("pic:"+vid, picurl, expiresAt=now+86400*7)`

## 7. 加载层铁律（照源码确认）

| 铁律 | 依据 |
|---|---|
| `getDependence()` 必须 `return []` | `Spider.init` 逐项 `download(item+".py")`，非空会给壳加依赖负担 |
| `getName()` 必须有 | app.py `getName` 直调 |
| 六接口返回值会被 `json.dumps` | app.py 各函数，**Python 侧直接 return dict，不要自己 dumps** |
| `categoryContent` 的 extend 已被 `json.loads` | app.py 里 `str2json(extend)` → **收到的是 dict 不是字符串** |
| `detailContent` 的 ids 已被 `json.loads` | 同上 → **收到的是 list** |
| `vipFlags` 已 `json.loads` | 同上 |
| `localProxy` 的 param 已 `json.loads` | 同上 → **收到的是 dict** |
| `searchContent(key, quick)` 两参时由壳补 `pg="1"` | Spider.java 有两个重载 |
| `siteKey` 被自动塞进 obj | `obj.put("siteKey", siteKey)` |
| ext 为 `[]` 时壳自动转 `{}` | `PyLoader.normalizeExt` |
| 实例按 siteKey 缓存 | `PyLoader` 的 `ConcurrentHashMap.computeIfAbsent` |
| `BaseLoader.clear()` 会 destroy 所有 py 源 | 切源/清缓存时实例上的东西会没 |
| 基类是**单例**（`__new__` + `_instance`） | base/spider.py |

## 8. 调试入口（排障神器）

```
http://127.0.0.1:<port>/debug/enable     # 开 SpiderDebug 日志
http://127.0.0.1:<port>/debug/logs        # 看日志
http://127.0.0.1:<port>/debug/diag/...    # 诊断
http://127.0.0.1:<port>/proxy?do=py&...   # 手动打 localProxy
```
`Proxy.java` 里有 `SpiderDebug.log("proxy", "request uri=%s method=%s do=%s params=%s")`
和 `proxy-stream` 的 firstByte / progress / speed 日志 → **「播放转圈」能在日志里看到是代理慢还是源返回空**。

## 9. 症状 → 源码定位速查

| 现象 | 大概率原因（源码依据） |
|---|---|
| 封面全空 / 播放转圈 + `Invalid proxy response` | `do=` 写错（必须 `py`）或无 `siteKey` 且 `recent==null` |
| 代理地址 404 | 端口写死（应 9978~9999 动态） |
| 代理能调但 body 乱码/空 | 返回 list <3 元素，或第4位传了字符串而非 dict |
| 二进制（PNG/KEY）损坏 | 未用第5位 base64=1，或 body 传 str 而非 bytes |
| 首页空白 | py 文件下载失败 / 语法错 / 类名不是 `Spider` / `getDependence` 非空 |
| ext 传 dict 但 init 收到异常 | 壳已把 `[]` 转 `{}`，init 要兼容 str/dict/JSON 串 |