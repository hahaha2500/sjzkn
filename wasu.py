# -*- coding: utf-8 -*-
"""
华数TV（wasu.cn）TVBox / FongMi type=3 Spider
站点：https://www.wasu.cn/wap/  （SPA 空壳，实为华数自研 CMS 接口）

【逆向要点 · 换站必读】
1. wap 页只有 375 字节 + 一个 Vue SPA bundle，真数据全在 JSON 接口里。
2. 主 API 域：https://mcspapp.5g.wasu.tv  （Tengine）
   搜索域：https://ups.5g.wasu.tv
   图床  ：https://mcsppic.5g.wasu.tv
   视频  ：https://video.5g.wasu.tv
3. 端点（全部免鉴权，GET 返回 {code,data,message}）：
   - 配置  /bvradio_app/hzhs/configServlet?siteId=10001&functionName=getAppConf
   - 导航  /bvradio_app/hzhs/recommendServlet?functionName=getNavigationBar&indexId=N&siteId=10001
            indexId=2 就是影视分类（电视剧/少儿/电影/短剧/栏目/慢直播）
            indexId=4 是榜单；indexId=43 搜索类型
   - 列表  /bvradio_app/hzhs/recommendServlet?functionName=getRecommond&modeId=N&siteId=10001
            ★无真分页：page=2 返回 0 条，page=1 给全量 → 本源按"全量一页"处理
   - 详情  /bvradio_app/hzhs/newsServlet?functionName=getCurrentNews&newsId=X&nodeId=Y&siteId=10001
            ★一次给全 vodList（每集）+ fileList（多清晰度）+ 明文 m3u8
   - 搜索  https://ups.5g.wasu.tv/rmp-user-suggest/10001/hzhs/searchServlet
           ?functionName=getNewsSearched&searchNewsType=3,4,5&siteId=10001&keyword=xx&page=1
           ★参数名是 keyword（searchKey/key/q 全部报 "requires query value"）
           ★分页参数是 page（pageNum/pageNo/currentPage 一律被忽略、返回第1页同内容）
           ★有真分页，每页 20 条；且直接返回 vodList 明文流
4. ★clickType 决定条目类型（踩坑点）：
   3=电影详情  4=电视剧详情  5=栏目  6=短视频
   9=分类标签 10=搜索标签 15=筛选条件 28=专题 999=运营位
   只收 clickType in (3,4) 才是影视；只收 4 会漏掉整个电影库。
5. 海报：pPic 竖图优先，为 null 时回退 hPic 横图。
   列表接口 pPic 常为 null（华数只在详情补图），所以 hPic 是列表海报主力。
6. 播放地址 vodList[].fileList[].playUrl 是明文 m3u8，按 type 区分清晰度：
   110 = 标清(720x408)  120 = 1080P(1920x1080)
7. ★★播放的真正钥匙（本次最关键突破）★★
   详情 vodList[].fileList[].playUrl 是【裸 m3u8，没有令牌】→ 直接播 403。
   真实流程必须先换流：
     POST /thirdApiFile/file/getPlayUrl
        body  = {"playUrl":"<裸m3u8>","platform":"wap"}
        header= content-type / siteId:10001 / launchChannel:wap_channel
                x-sign = Base64(HmacSHA256(JSON.stringify(body), Base64Decode(SIGN_KEY_WAP)))
     返回 {"code":200,"data":{"playUrl":"https://bdwapvideo.5g.wasu.tv/.../playlist.m3u8?auth_key=<expiry>-0-0-<md5>"}}
   auth_key 首段是过期时间戳 → ★必须现取现播，不能长期缓存。
   换流后裸请求即可 200（无需 Referer/UA），分片同样带各自 auth_key。
8. x-sign 签名算法（逆向自 Qt 的 Gy/Qy → zc → Xy）：
   JS: zc(data, siteId) = Xy(JSON.stringify(data), siteId)
       Xy(msg, t) = Base64( HmacSHA256(msg, Base64Decode( t ? KEY_PC : KEY_WAP )) )
   密钥（Base64 形式，明文是 UUID）：
       KEY_WAP = "OTUxOGJiMWItY2NkYS00OTY4LWIwZDAtNDlkMTlkZDEzZWNl"  (wap_channel, siteId=10001)
       KEY_PC  = "M2VjYzkwZmUtZGE1NC00YmQ2LThkMmUtNmU3ODIwZmJlNzZh"  (web_channel, siteId=1000101)
   ★注意 JSON.stringify 的键顺序必须与 JS 一致：{"playUrl":...,"platform":"wap"}
   ★注意没有 x-sign 时接口返回 {"code":500,"message":null,"data":null}（静默失败，不报错）
"""
import json
import re
import base64
import gzip
import hmac
import hashlib
import threading
import time
import urllib.parse as _up
import urllib.request as _ur
import urllib.error as _ue

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        pass

_UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Mobile Safari/537.36")
_REFER = "https://www.wasu.cn/wap/"

API = "https://mcspapp.5g.wasu.tv"
UPS = "https://ups.5g.wasu.tv"
EXCHANGE = API + "/thirdApiFile/file/getPlayUrl"
PIC = "https://mcsppic.5g.wasu.tv"

# x-sign 签名密钥（Base64，明文是 UUID）。缺 x-sign 时接口静默返回 code:500
SIGN_KEY_WAP = "OTUxOGJiMWItY2NkYS00OTY4LWIwZDAtNDlkMTlkZDEzZWNl"
SIGN_KEY_PC = "M2VjYzkwZmUtZGE1NC00YmQ2LThkMmUtNmU3ODIwZmJlNzZh"


def _xsign(data, pc=False):
    """Base64(HmacSHA256(JSON.stringify(data), Base64Decode(key)))
    ★键顺序必须与前端一致：{"playUrl":...,"platform":"wap"}"""
    try:
        key = base64.b64decode(SIGN_KEY_PC if pc else SIGN_KEY_WAP)
        msg = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return base64.b64encode(
            hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()).decode()
    except Exception:
        return ""

# 分类：一个 TVBox 分类 = 若干栏目 modeId 合并去重（站点无真分页）
CLASSES = [
    ("电视剧", "tv",   [103, 101, 117]),
    ("少儿",   "kid",  [104, 118]),
    ("电影",   "mv",   [102, 116, 131]),
    ("短剧",   "duan", [1025, 1067]),
    ("热播榜", "hot",  [115]),
]
CLS_ID = dict((n, i) for i, (n, _k, _m) in enumerate(CLASSES))
CLS_BY_ID = dict((i, (n, k, m)) for i, (n, k, m) in enumerate(CLASSES))
# 名称 -> modeId 列表（兼容分类被壳子当名字传回）
CLS_BY_NAME = dict((n, m) for n, _k, m in CLASSES)
CLS_BY_KEY = dict((k, m) for n, k, m in CLASSES)

# 影视条目类型（★只收这两个，见逆向要点 4）
CT_MOVIE, CT_TELE = 3, 4

_CACHE = {}
_CACHE_LOCK = threading.RLock()
_SESSION = None
_SESSION_LOCK = threading.RLock()


def _sess():
    """模块级连接池：壳子可能每次调用都 new Spider，实例级池会全废。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent": _UA, "Referer": _REFER,
                              "Accept": "application/json, text/plain, */*"})
            try:
                from requests.adapters import HTTPAdapter
                ad = HTTPAdapter(pool_connections=12, pool_maxsize=24, max_retries=2)
                s.mount("https://", ad)
                s.mount("http://", ad)
            except Exception:
                pass
            _SESSION = s
        return _SESSION


class _Stat(object):
    def __init__(self):
        self.ok = 0
        self.fail = 0

    def hit(self, good):
        if good:
            self.ok += 1
        else:
            self.fail += 1


STAT = _Stat()


def _cache_get(key, ttl):
    with _CACHE_LOCK:
        it = _CACHE.get(key)
        if not it:
            return None
        ts, val = it
        if time.time() - ts > ttl:
            _CACHE.pop(key, None)
            return None
        return val


def _cache_put(key, val):
    with _CACHE_LOCK:
        if len(_CACHE) > 600:
            _CACHE.clear()
        _CACHE[key] = (time.time(), val)


def _unq(s):
    """剥 query 后的干净 URL 片段，用作缓存键。"""
    return s.split("?")[0]


def _http(url, timeout=15, refer=None, data=None):
    """唯一网络出口。返回 str，失败抛异常（上层一律 try/except 兜底）。"""
    hdr = {"User-Agent": _UA, "Referer": refer or _REFER,
           "Accept": "application/json, text/plain, */*",
           "Accept-Encoding": "gzip"}
    req = _ur.Request(url, headers=hdr, data=data)
    raw = _ur.urlopen(req, timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", "ignore")


def _api(url, ttl=300, timeout=15):
    """带 TTL 缓存的 API GET。"""
    # ★缓存键必须用【完整 URL 含 query】
    # 踩坑：曾用 split("?")[0] 做键，导致所有 recommendServlet 请求（只差 modeId）
    #       撞成同一个键，5 个分类返回完全相同内容。
    ck = "api:" + url
    hit = _cache_get(ck, ttl)
    if hit is not None:
        return hit
    try:
        txt = _http(url, timeout=timeout)
    except _ue.HTTPError:
        raise
    except Exception:
        try:
            r = _sess().get(url, timeout=timeout)
            r.raise_for_status()
            txt = r.text
        except Exception as e:
            STAT.hit(False)
            return None
    try:
        d = json.loads(txt)
    except Exception:
        STAT.hit(False)
        return None
    if not isinstance(d, dict) or d.get("code") not in (200, "200", None):
        STAT.hit(False)
        return None
    STAT.hit(True)
    _cache_put(ck, d)
    return d


def _clean(s, lim=0):
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (_up.unquote(s).replace("\r", " ").replace("\n", " ")
         .replace("\t", " ").replace(" ", " "))
    s = re.sub(r"\s{2,}", " ", s).strip()
    # $ 是 TVBox 分隔符，标题/集名里的 $ 必须清掉，否则分集塌陷
    s = s.replace("$", "＄").replace("#", "＃")
    if lim and len(s) > lim:
        s = s[:lim]
    return s


def _pic(it):
    """海报：竖图优先，null 时回退横图。"""
    for k in ("pPic", "hPic"):
        v = it.get(k)
        if v and isinstance(v, str) and v.startswith("http") and "null" not in v:
            return v
    return ""


def _detail_url(node_id, news_id):
    return ("%s/bvradio_app/hzhs/newsServlet?functionName=getCurrentNews"
            "&newsId=%s&nodeId=%s&siteId=10001" % (API, news_id, node_id))


def _parse_recommend(mid):
    """栏目 modeId -> 条目列表。"""
    url = ("%s/bvradio_app/hzhs/recommendServlet?functionName=getRecommond"
           "&modeId=%s&siteId=10001" % (API, mid))
    d = _api(url, ttl=600)
    if not d:
        return []
    out = []
    for grp in (d.get("data") or []):
        if not isinstance(grp, dict):
            continue
        # 影视可能在 manualList，也可能在嵌套 childModels
        pools = [grp.get("manualList") or []]
        for ch in (grp.get("childModels") or []):
            if isinstance(ch, dict):
                pools.append(ch.get("manualList") or [])
        for pool in pools:
            for it in pool:
                if not isinstance(it, dict):
                    continue
                if it.get("clickType") not in (CT_MOVIE, CT_TELE):
                    continue          # ★过滤标签/运营位
                nid = str(it.get("id") or "")
                if "," not in nid:
                    continue          # 影视 id 必须是 "nodeId,newsId"
                node_id, news_id = nid.split(",", 1)
                out.append({
                    "node_id": node_id,
                    "news_id": news_id,
                    "title": _clean(it.get("title"), 60) or "未命名",
                    "pic": _pic(it),
                    "remarks": _clean(it.get("episodeDesc") or it.get("cornerMark"), 30),
                })
    return out


def _norm_tid(tid):
    """壳子可能传数字下标 / 分类名 / key / 完整 URL，全部归一成 modeId 列表。"""
    if tid is None:
        return None
    t = str(tid).strip()
    if t in CLS_BY_ID:
        return CLS_BY_ID[t][2]
    if t in CLS_BY_NAME:
        return CLS_BY_NAME[t]
    if t in CLS_BY_KEY:
        return CLS_BY_KEY[t]
    m = re.search(r"modeId=(\d+)", t)
    if m:
        return [int(m.group(1))]
    if t.isdigit():
        i = int(t)
        if i in CLS_BY_ID:
            return CLS_BY_ID[i][2]
    return None


def _norm_pid(pid):
    """vod_id 段规（@@ 分隔）：
       m@@<quote(m3u8)>@@nodeId@@newsId@@ep     直连（带回流地址，免二次请求）
       e@@0@@nodeId@@newsId@@ep                直连（重取详情定位该集）
       s@@0@@nodeId@@newsId@@ep                嗅探（回详情原页交 App 嗅探）
       d@@nodeId@@newsId@@0                    详情入口
       兼容旧的 2/3/4 段裸格式 nodeId@@newsId@@ep / "nodeId,newsId"
    """
    p = str(pid or "").split("@@")
    if len(p) == 1 and "," in p[0]:
        a, b = p[0].split(",", 1)
        return "e", a, b, "0"
    if len(p) >= 5:
        return p[0], p[2], p[3], p[4]
    while len(p) < 4:
        p.append("0" if len(p) == 1 else "")
    if len(p) == 4:
        # 4 段：d@@node@@news@@ep  → 当详情入口
        return "e", p[1], p[2], p[3]
    return "e", "108", p[0], "0"


def _exchange(play_url, timeout=15):
    """★核心：把详情里的【裸 m3u8】换成【带 auth_key 令牌的】真实流地址。
    没有这一步，播放地址一律 403（这是本源的真正命门）。"""
    if not play_url or not str(play_url).startswith("http"):
        return ""
    if "auth_key=" in str(play_url):
        return str(play_url)          # 已有令牌，不重复换
    data = {"playUrl": str(play_url), "platform": "wap"}
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    hdr = {
        "User-Agent": _UA,
        "Referer": _REFER,
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json, text/plain, */*",
        "siteId": "10001",
        "launchChannel": "wap_channel",
        "platform": "wap",
        "x-sign": _xsign(data),
    }
    raw = b""
    for getter in ("_ur", "req"):
        try:
            if getter == "_ur":
                req = _ur.Request(EXCHANGE, data=body, headers=hdr)
            else:
                r = _sess().post(EXCHANGE, data=body, headers=hdr, timeout=timeout)
                r.raise_for_status()
                raw = r.content
            if not raw:
                req = _ur.Request(EXCHANGE, data=body, headers=hdr)
            raw = _ur.urlopen(req, timeout=timeout).read()
            break
        except Exception:
            continue
    if not raw:
        return ""
    try:
        d = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return ""
    if not isinstance(d, dict) or d.get("code") not in (200, "200"):
        return ""
    node = d.get("data") or {}
    if isinstance(node, dict):
        for k in ("playUrl", "url", "playurl"):
            v = node.get(k)
            if v and isinstance(v, str) and v.startswith("http"):
                return v
    return ""


class Spider(_BaseSpider):
    """华数TV 点播源"""

    def __init__(self):
        try:
            _BaseSpider.__init__(self)
        except Exception:
            pass
        self.name = "华数TV"
        self.host = "https://www.wasu.cn"
        self.proxy = None      # extend={"proxy":"http://127.0.0.1:7890"}
        try:
            self.ext = json.loads(_str_arg(extend_src)) if False else {}
        except Exception:
            self.ext = {}

    # ---------- TVBox 必需方法 ----------
    def getName(self):
        return self.name

    def getDependence(self):
        # ★必须返回 []：返回 ["requests"] 会让壳子尝试装依赖，失败即整源空白
        return []

    def isVideoFormat(self, url):
        try:
            u = str(url).lower()
        except Exception:
            return False
        return any(e in u for e in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"))

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        if isinstance(extend, dict):
            cfg = extend
        else:
            cfg = {}
            s = str(extend or "").strip()
            if s.startswith("{"):
                try:
                    cfg = json.loads(s)
                except Exception:
                    cfg = {}
            elif s:
                cfg = {"host": s.rstrip("/")}
        if cfg.get("name"):
            self.name = _clean(cfg["name"], 20)
        if cfg.get("host"):
            self.host = str(cfg["host"]).rstrip("/")
        if cfg.get("proxy"):
            self.proxy = str(cfg["proxy"])
        if cfg.get("reset"):
            with _CACHE_LOCK:
                _CACHE.clear()
        return self

    # ---------- 首页 ----------
    def homeContent(self, filter=False):
        cls = [{"type_id": str(i), "type_name": n} for i, (n, _k, _m) in enumerate(CLASSES)]
        fl = {"电影": [], "电视剧": [], "少儿": [], "短剧": [], "热播榜": []}
        for n, _k, modes in CLASSES:
            fl[n] = [{"n": "全部", "v": ""}]
        res = {"class": cls}
        if filter:
            res["filters"] = fl
        try:
            res["list"] = self.homeVideoContent().get("list", [])
        except Exception:
            res["list"] = []
        return res

    def homeVideoContent(self):
        # 首页推荐 = 电视剧 + 电影 + 少儿 各取前若干
        out = []
        for mid in (103, 102, 104):
            for it in _parse_recommend(mid):
                out.append({
                    "vod_id": "s@@%s@@%s@@0" % (it["node_id"], it["news_id"]),
                    "vod_name": it["title"],
                    "vod_pic": it["pic"],
                    "vod_remarks": it["remarks"],
                })
        if not out:
            return {"list": [_diag("首页推荐拉取失败", "接口无数据或网络不通，"
                                          "可 extend 换 host 或稍后再试")]}
        return {"list": out[:60]}

    # ---------- 分类 ----------
    def categoryContent(self, tid, pg, filter=False, extend=None):
        modes = _norm_tid(tid)
        if not modes:
            return self._empty(pg)
        # extend 传筛选：{"modeId":116} 直接指定栏目
        if isinstance(extend, dict) and extend.get("modeId"):
            modes = [int(extend["modeId"])]
        elif isinstance(extend, str) and extend.strip().isdigit():
            modes = [int(extend.strip())]
        items = []
        for mid in modes:
            items.extend(_parse_recommend(mid))
        seen = set()
        uniq = []
        for it in items:
            k = it["news_id"]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(it)
        if not uniq:
            return self._empty(pg, _diag("分类为空", "modeId=%s 无影视数据" % modes))
        lst = [{
            "vod_id": "c@@%s@@%s@@0" % (it["node_id"], it["news_id"]),
            "vod_name": it["title"],
            "vod_pic": it["pic"],
            "vod_remarks": it["remarks"],
        } for it in uniq]
        # ★站点无真分页（page=2 返回 0），诚实返回单页
        return {"list": lst, "page": 1, "pagecount": 1, "limit": len(lst),
                "total": len(lst)}

    # ---------- 搜索（有真分页） ----------
    def searchContent(self, key, quick=False, pg="1"):
        kw = _clean(key, 40)
        if not kw:
            return self._empty(1)
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        # ★分页参数是 page，不是 pageNum（pageNum/pageNo/currentPage 全部被忽略，
        #   返回第1页同内容，会造成"翻页没反应"）
        url = ("%s/rmp-user-suggest/10001/hzhs/searchServlet?functionName=getNewsSearched"
               "&searchNewsType=3,4,5&siteId=10001&keyword=%s&page=%d"
               % (UPS, _up.quote(kw), pg))
        d = _api(url, ttl=240)
        rows = (d or {}).get("data") or []
        lst = []
        for it in rows:
            if not isinstance(it, dict):
                continue
            nid, vid = it.get("nodeId"), it.get("newsId")
            if nid is None or vid is None:
                continue
            pic = _pic(it)
            if not pic:
                vl = it.get("vodList") or []
                if vl and isinstance(vl[0], dict):
                    pic = vl[0].get("img") or ""
            lst.append({
                "vod_id": "f@@%s@@%s@@0" % (nid, vid),
                "vod_name": _clean(it.get("title"), 60) or "未命名",
                "vod_pic": pic,
                "vod_remarks": _clean(it.get("episodeDesc"), 30),
            })
        if not lst:
            return self._empty(pg)
        # 满页才给下一页，避免无限空翻
        pc = pg + 1 if len(lst) >= 20 else pg
        return {"list": lst, "page": pg, "pagecount": pc,
                "limit": len(lst), "total": len(lst) * pc}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        raw = ids[0] if isinstance(ids, (list, tuple)) else ids
        mode, node_id, news_id, _ep = _norm_pid(raw)
        d = _api(_detail_url(node_id, news_id), ttl=900)
        data = (d or {}).get("data")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            data = {}
        title = _clean(data.get("title"), 60) or ("%s-%s" % (node_id, news_id))
        vl = data.get("vodList") or []

        # 按清晰度归组：华数·高清 / 华数·标清
        hi, sd, sn = [], [], []
        for idx, v in enumerate(vl):
            if not isinstance(v, dict):
                continue
            files = v.get("fileList") or []
            urls = [f.get("playUrl") for f in files
                    if isinstance(f, dict) and f.get("playUrl")]
            if not urls:
                continue
            ep = _clean(v.get("episode"), 12)
            # ★华数的 episode 字段本身常已是"第1集"，直接拼会变成"第第1集集"
            if not ep:
                name = "第%d集" % (idx + 1)
            elif re.match(r"^第?\s*[\d一二三四五六七八九十百]+\s*[集期话章]?$", ep):
                name = ep if "集" in ep else (ep + "集")
            else:
                name = "第%d集" % (idx + 1)
            name = _clean(name, 20)
            if name in hi or name in sd:
                name = "%s(%d)" % (name, idx + 1)
            # 1080P 优先（type=120）
            pick_hd = None
            for f in files:
                if isinstance(f, dict) and f.get("type") == 120 and f.get("playUrl"):
                    pick_hd = f["playUrl"]
                    break
            hd = pick_hd or urls[-1]
            # 5 段制：m=直连带回流地址（免二次请求，最快）
            hi.append("%s$m@@%s@@%s@@%s@@%d"
                      % (name, _up.quote(hd, safe=""), node_id, news_id, idx))
            sd.append("%s$m@@%s@@%s@@%s@@%d"
                      % (name, _up.quote(urls[0], safe=""), node_id, news_id, idx))
            sn.append("%s$s@@0@@%s@@%s@@%d" % (name, node_id, news_id, idx))

        from_lines = ["华数·高清", "华数·标清", "华数·原页嗅探"]
        play_url = "$$$".join(["#".join(hi), "#".join(sd), "#".join(sn)]) if hi else ""
        if not hi:
            from_lines = ["华数·原页嗅探"]
            play_url = "#".join(["本片暂无播放数据$"])

        pic = _pic(data) or ""
        if not pic and vl and isinstance(vl[0], dict):
            pic = vl[0].get("img") or ""
        remarks = _clean(data.get("episodeDesc"), 30)
        year = _clean(data.get("yearTag"), 8)
        if year and year not in remarks:
            remarks = (year + " " + remarks).strip() if remarks else year

        vod = {
            "vod_id": "d@@%s@@%s@@0" % (node_id, news_id),
            "vod_name": title,
            "vod_pic": pic,
            "vod_remarks": remarks,
            "type_name": _clean(data.get("newsTypeStr") or data.get("nodeName"), 12),
            "vod_year": year,
            "vod_area": _clean(data.get("countryTag"), 20),
            "vod_actor": _clean(data.get("actor"), 120),
            "vod_director": _clean(data.get("director"), 60),
            "vod_content": _clean(data.get("newsAbstract") or data.get("content"), 400),
            "vod_play_from": "$$$".join(from_lines),
            "vod_play_url": play_url,
        }
        return {"list": [vod]}

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags=None):
        try:
            mode, node_id, news_id, ep = _norm_pid(id)
            ep = int(ep or 0)
        except Exception:
            return _empty_play()
        fl = str(flag or "")
        # 嗅探线：回详情原页交 App 内建嗅探
        if "嗅探" in fl or mode == "s":
            return {"parse": 0, "url": _detail_url(node_id, news_id), "header": _ph()}

        # 收集候选裸地址：pid 自带 → 详情定位
        cands = []
        if mode == "m":
            u = str(id).split("@@", 1)[0]
            u = u[2:] if u[:2] == "m@" else u
            u = _up.unquote(u)
            if u.startswith("http"):
                cands.append(u)
        if not cands or mode != "m":
            d = _api(_detail_url(node_id, news_id), ttl=300)
            data = (d or {}).get("data")
            if isinstance(data, list):
                data = data[0] if data else None
            if isinstance(data, dict):
                vl = data.get("vodList") or []
                if 0 <= ep < len(vl) and isinstance(vl[ep], dict):
                    files = vl[ep].get("fileList") or []
                    order = (110,) if "标清" in fl else (120, 110)
                    for ty in order:
                        for f in files:
                            if isinstance(f, dict) and f.get("type") == ty and f.get("playUrl"):
                                cands.append(f["playUrl"])
                    for f in files:
                        if isinstance(f, dict) and f.get("playUrl"):
                            cands.append(f["playUrl"])

        # ★逐个换流：拿到带 auth_key 的地址才算成功
        for u in cands:
            if not (isinstance(u, str) and u.startswith("http")):
                continue
            real = _exchange(u)
            if real:
                return {"parse": 0, "url": real, "header": _ph()}
        # 换流全失败：仍回裸地址让播放器自己试（万一后端放行）
        for u in cands:
            if isinstance(u, str) and u.startswith("http"):
                return {"parse": 0, "url": u, "header": _ph()}
        return _empty_play()

    # ---------- 本地代理（图片中转，壳子用 do=py 回调） ----------
    def localProxy(self, param=None):
        try:
            p = param
            if isinstance(p, str):
                s = p.strip()
                if s.startswith("{"):
                    try:
                        p = json.loads(s)
                    except Exception:
                        p = {"url": s}
                else:
                    p = {"url": s}
            if not isinstance(p, dict):
                return [404, "text/plain", b"", {}]
            raw = p.get("url") or ""
            if "base64" in p or "b64" in str(p.get("type") or ""):
                try:
                    raw = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "ignore")
                except Exception:
                    pass
            raw = _up.unquote(raw)
            if not raw.startswith("http"):
                return [404, "text/plain", b"", {}]
            key = "img:" + _unq(raw)
            hit = _cache_get(key, 3600)
            if hit is not None:
                return [200, "image/jpeg", hit, {"Cache-Control": "max-age=86400"}]
            req = _ur.Request(raw, headers={"User-Agent": _UA, "Referer": self.host + "/"})
            body = _ur.urlopen(req, timeout=20).read()
            if not body:
                return [404, "text/plain", b"", {}]
            _cache_put(key, body)
            return [200, "image/jpeg", body, {"Cache-Control": "max-age=86400"}]
        except Exception:
            # ★空体返 404，不返 200 空图（否则壳子缓存坏图）
            return [404, "text/plain", b"", {}]

    # ---------- 工具 ----------
    def _empty(self, pg=1, diag=None):
        d = {"list": [], "page": int(pg or 1), "pagecount": int(pg or 1),
             "limit": 0, "total": 0}
        if diag:
            d["list"] = [diag]
        return d


def _ph():
    """播放头：一律 dict（header 必须 dict，不能 json 字符串）。"""
    return {"User-Agent": _UA, "Referer": "https://www.wasu.cn/"}


def _empty_play():
    # parse:0 + 详情原页，交 App 内建嗅探兜底
    return {"parse": 0, "url": "", "header": _ph()}


def _diag(tag, msg):
    return {"vod_id": "diag@@0@@0@@0", "vod_name": "[诊断] " + tag,
            "vod_pic": "", "vod_remarks": "点我无用"}
