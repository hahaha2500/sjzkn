# -*- coding: utf-8 -*-
# 欲望之眼 ywatomiczenxgridhub.xyz  TVBox/FongMi type=3 Python Spider
# 站型：MacCMS 变体自写模板（default），无采集接口(closed)，无详情页（详情=播放页合一）
# 分类 /index.php/vod/type/id/{tid}.html  分页 /index.php/vod/type/id/{tid}/page/{pg}.html?orderby=time
# 播放 /index.php/vod/play/id/{id}/sid/1/nid/1.html  内 var player_aaaa={"encrypt":0,"url":"明文m3u8"}
# 搜索 /index.php/vod/search/page/{pg}/wd/{关键词}.html?orderby=time
import re, json, base64, gzip, zlib, random, threading, time as _t
from urllib.parse import quote, urljoin, urlparse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def getProxyUrl(self, local=True):
            try:
                from com.github.catvod import Proxy
                return Proxy.getUrl(True)
            except Exception:
                return ""
        def setCache(self, k, v, t=0): pass
        def getCache(self, k): return ""
        def delCache(self, k): pass

HOSTS = [
    "https://ywatomiczenxgridhub.xyz",
]
SITE_NAME = "欲望之眼"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 模块级缓存：壳可能每次 new Spider，实例级会全废
_CACHE = {}
_CK = threading.Lock()
_PICS = {}
_SESS = threading.local()

CLASSES = [
    ("35", "中文字幕"), ("33", "日本女优"), ("43", "国产视频"),
    ("53", "国产传媒"), ("29", "人妖"), ("47", "萝莉女孩"),
    ("23", "女同性爱"), ("55", "三级片"), ("25", "重口味"),
    ("39", "欧美视频"),
]

RE_CARD = re.compile(
    r'<div[^>]*class="model-list-item[^"]*"[^>]*>\s*<a[^>]*class="model-list-item-link[^"]*"[^>]*'
    r'href="(/index\.php/vod/play/id/(\d+)/[^"]*)"[^>]*>(.*?)</a>(.*?)(?=<div[^>]*class="model-list-item|</div>\s*</div>\s*</div>|<footer)', re.S)
RE_IMG = re.compile(r'(?:data-original|data-src|src)\s*=\s*"(https?://[^"]+?\.(?:jpg|jpeg|png|webp)[^"]*)"', re.I)
RE_NAME = re.compile(r'class="[^"]*model-name[^"]*"[^>]*>(.*?)</span>', re.S)
RE_TAG = re.compile(r'<span\s+class="[^"]*ModelListItemBadge[^"]*"[^>]*>(.*?)</span>', re.S)
RE_TITLE = re.compile(r'<span[^>]*class="text-title-l1[^"]*"[^>]*>(.*?)</h1>', re.S)
RE_CLS = re.compile(r'href="(/index\.php/vod/type/id/(\d+)\.html)"[^>]*>(.{0,160}?)</a>', re.S)
RE_PLAYER = re.compile(r'var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>', re.S)
RE_PLAYER2 = re.compile(r'var\s+player_data\s*=\s*(\{.*?\})\s*</script>', re.S)
RE_M3U8 = re.compile(r'(https?:\\?/\\?/[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+\.m3u8)', re.I)
RE_SEARCHPG = re.compile(r'/index\.php/vod/search/page/(\d+)/wd/[^"]*\.html')
RE_CATPG = re.compile(r'/index\.php/vod/type/id/\d+/page/(\d+)\.html')


def _decomp(raw, hd):
    if not raw:
        return b""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            try:
                raw = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
            except Exception:
                pass
    elif raw[:1] == b"\x78":
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
    return raw


def _session():
    s = getattr(_SESS, "s", None)
    if s is None:
        try:
            import requests
            from requests.adapters import HTTPAdapter
            try:
                from urllib3.util.retry import Retry
                r = Retry(total=2, connect=2, read=2, backoff_factor=0.4,
                          status_forcelist=[429, 500, 502, 503, 504])
            except Exception:
                r = None
            s = requests.Session()
            if r is not None:
                ad = HTTPAdapter(max_retries=r, pool_connections=12, pool_maxsize=24)
                s.mount("https://", ad)
                s.mount("http://", ad)
            _SESS.s = s
        except Exception:
            s = None
            _SESS.s = None
    return s


class Spider(BaseSpider):

    def __init__(self):
        self.host = HOSTS[0]
        self.headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        try:
            self._load_ext("")
        except Exception:
            pass
        try:
            self.setCache("ywa_cfg", self.host, 86400 * 3)
        except Exception:
            pass

    # ---------------- 基础 ----------------
    def getName(self):
        return SITE_NAME

    def isVideoFormat(self, url):
        return any(ext in (url or "").lower() for ext in
                   ['.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.mpg'])

    def manualVideoCheck(self):
        return False

    def getDependence(self):
        return []

    def _load_ext(self, extend):
        if not extend:
            return
        try:
            if isinstance(extend, str):
                extend = json.loads(extend) if extend.strip().startswith("{") else {"host": extend}
            if isinstance(extend, dict):
                h = extend.get("host") or extend.get("url") or ""
                if h:
                    self.host = h.strip().rstrip("/")
        except Exception:
            pass

    def init(self, extend=""):
        self._load_ext(extend)

    def action(self, action):
        return None

    def _cache_get(self, k):
        with _CK:
            v = _CACHE.get(k)
        if not v:
            return None
        ts, val = v
        if ts and ts < _t.time():
            return None
        return val

    def _cache_put(self, k, val, ttl=0):
        with _CK:
            if len(_CACHE) > 400:
                _CACHE.clear()
            _CACHE[k] = ((_t.time() + ttl) if ttl else 0, val)
        return val

    def _get(self, path, tries=3, ref=None):
        url = path if path.startswith("http") else self.host + path
        for i in range(tries):
            try:
                hd = dict(self.headers)
                hd["Referer"] = ref or (self.host + "/")
                s = _session()
                if s is not None:
                    r = s.get(url, headers=hd, timeout=(8, 22), allow_redirects=True)
                    body = _decomp(r.content, r.headers)
                    if r.status_code == 200 and body:
                        return body.decode("utf-8", "ignore")
                else:
                    import urllib.request as _u
                    req = _u.Request(url, headers=hd)
                    with _u.urlopen(req, timeout=22) as resp:
                        raw = resp.read()
                    txt = _decomp(raw, None).decode("utf-8", "ignore")
                    if txt:
                        return txt
            except Exception:
                pass
            _t.sleep(0.4 * (i + 1))
        return ""

    # ---------------- 解析 ----------------
    @staticmethod
    def _clean(s):
        if not s:
            return ""
        s = re.sub(r'<[^>]+>', '', s)
        s = s.replace("&amp;", "&").replace("&nbsp;", " ").replace("&quot;", '"')
        return re.sub(r'\s+', ' ', s).strip()

    def _cards(self, html, limit=60):
        out, seen = [], set()
        for m in RE_CARD.finditer(html):
            href = m.group(1)
            vid = m.group(2)
            if vid in seen:
                continue
            inner = m.group(3) + m.group(4)
            pic = ""
            mi = RE_IMG.search(inner)
            if mi:
                pic = mi.group(1).replace("\\/", "/")
            name = ""
            mn = RE_NAME.search(inner)
            if mn:
                name = self._clean(mn.group(1))
            tag = ""
            mt = RE_TAG.search(inner)
            if mt:
                tag = self._clean(mt.group(1))
            if not name:
                name = vid
            seen.add(vid)
            if pic:
                _PICS[vid] = pic
            out.append({"vod_id": vid, "vod_name": name,
                        "vod_pic": pic, "vod_remarks": tag})
            if len(out) >= limit:
                break
        return out

    def _pagecount(self, html, cur=1):
        nums = [int(x) for x in RE_CATPG.findall(html)] + [int(x) for x in RE_SEARCHPG.findall(html)]
        nums = [n for n in nums if n > 0]
        if nums:
            return max(max(nums), cur)
        if len(RE_CARD.findall(html)) < 40:
            return cur
        return cur + 1

    def _fill(self, lst):
        return {"list": lst, "page": 1, "pagecount": 1, "limit": len(lst), "total": len(lst)}

    # ---------------- 六接口 ----------------
    def homeContent(self, filter=False):
        self.ensureHome()
        try:
            result = {"class": [{"type_id": t, "type_name": n} for t, n in CLASSES]}
        except Exception:
            result = {}
        html = self._cache_get("home:html")
        lst = self._cards(html, 36) if html else []
        if lst:
            result["list"] = lst
        if filter:
            fs = [{"key": "orderby", "name": "排序", "value": [
                {"n": "最新", "v": "time"}, {"n": "热门", "v": "hits"},
                {"n": "评分", "v": "score"}, {"n": "推荐", "v": "up"}]}]
            result["filters"] = {t: [dict(fs[0])] for t, _ in CLASSES}
        return result

    def ensureHome(self, force=False):
        html = None if force else self._cache_get("home:html")
        if not html:
            html = self._get("/index.php/index/index/page/1.html?orderby=time")
            if html:
                self._cache_put("home:html", html, 180)
        return html

    def homeVideoContent(self):
        self.ensureHome()
        lst = self._cards(self._cache_get("home:html") or "", 24)
        return {"list": lst, "page": 1, "pagecount": 1,
                "limit": len(lst), "total": len(lst)}

    def categoryContent(self, tid, pg, filter=True, extend=None):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        tid = str(tid).split("@@")[0]
        if tid.startswith("http"):
            base = tid.rstrip("/")
            tidm = re.search(r'/id/(\d+)', base)
            tid = tidm.group(1) if tidm else "23"
        ext = extend if isinstance(extend, dict) else {}
        order = str(ext.get("orderby") or "time")
        if pg == 1:
            path = "/index.php/vod/type/id/%s.html?orderby=%s" % (tid, order)
        else:
            path = "/index.php/vod/type/id/%s/page/%d.html?orderby=%s" % (tid, pg, order)
        key = "cat:%s:%d:%s" % (tid, pg, order)
        html = self._cache_get(key)
        if not html:
            html = self._get(path)
            if html:
                self._cache_put(key, html, 180)
        lst = self._cards(html, 60) if html else []
        if pg > 1 and not lst:
            html = ""
        pc = self._pagecount(html, pg) if lst else pg
        return {"list": lst, "page": pg, "pagecount": max(pc, 1),
                "limit": len(lst), "total": len(lst)}

    def detailContent(self, ids):
        try:
            idlist = ids if isinstance(ids, list) else (
                ids.split(",") if isinstance(ids, str) else [])
        except Exception:
            idlist = []
        vods = []
        for vid in idlist:
            vid = str(vid).split("@@")[0].strip()
            if not vid.isdigit():
                continue
            path = "/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid
            html = self._cache_get("det:%s" % vid)
            if not html:
                html = self._get(path)
                if html:
                    self._cache_put("det:%s" % vid, html, 300)
            info = self._player_info(html)
            name = info.get("vod_name") or ""
            pic = _PICS.get(vid, "")
            if not pic and info.get("url"):
                pic = self._guess_pic(info["url"], vid)
            if name:
                name = name[:120]
            vods.append({
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": pic,
                "vod_remarks": info.get("from", ""),
                "vod_year": "",
                "vod_area": info.get("vod_class", ""),
                "vod_actor": info.get("vod_actor", ""),
                "vod_director": info.get("vod_director", ""),
                "vod_content": "分类:%s 线路:%s" % (info.get("vod_class", ""),
                                                   info.get("from", "") or "默认"),
                "vod_play_from": "欲望之眼·直连$$$欲望之眼·嗅探",
                "vod_play_url": "正片$m$$$正片$s0",
            })
        return {"list": vods}

    @staticmethod
    def _guess_pic(m3u8, vid):
        try:
            p = urlparse(m3u8)
            seg = p.path.split("/")
            if len(seg) >= 3:
                return "%s://%s/%s/%s/1.jpg" % (p.scheme, p.netloc, seg[1], seg[2])
        except Exception:
            pass
        return ""

    def _player_info(self, html):
        out = {}
        if not html:
            return out
        m = RE_PLAYER.search(html) or RE_PLAYER2.search(html)
        if not m:
            return out
        try:
            raw = m.group(1).replace("\\/", "/")
            raw = re.sub(r"\\(?![\"\\/bfnrtu])", r"\\", raw)
            d = json.loads(raw)
            out["url"] = d.get("url", "")
            out["from"] = d.get("from", "")
            vd = d.get("vod_data") or {}
            out["vod_name"] = vd.get("vod_name", "")
            out["vod_class"] = vd.get("vod_class", "")
            out["vod_actor"] = vd.get("vod_actor", "")
            out["vod_director"] = vd.get("vod_director", "")
        except Exception:
            mm = RE_M3U8.search(m.group(1))
            if mm:
                out["url"] = mm.group(1).replace("\\/", "/")
        if not out.get("vod_name"):
            mt = RE_TITLE.search(html)
            if mt:
                out["vod_name"] = self._clean(mt.group(1))
        return out

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        try:
            kw = str(key or "").strip()
        except Exception:
            kw = ""
        if not kw:
            return {"list": [], "page": pg, "pagecount": 1, "limit": 0, "total": 0}
        if pg == 1:
            path = "/index.php/vod/search/wd/%s.html?orderby=time" % quote(kw, safe="")
        else:
            path = "/index.php/vod/search/page/%d/wd/%s.html?orderby=time" % (pg, quote(kw, safe=""))
        key2 = "sea:%s:%d" % (kw, pg)
        html = self._cache_get(key2)
        if not html:
            html = self._get(path)
            if html:
                self._cache_put(key2, html, 180)
        lst = self._cards(html, 60) if html else []
        pc = self._pagecount(html, pg) if lst else pg
        return {"list": lst, "page": pg, "pagecount": max(pc, 1),
                "limit": len(lst), "total": len(lst)}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            pid = str(id)
        except Exception:
            pid = ""
        mode = "m"
        vid = pid
        if "@@" in pid:
            mode, vid = pid.split("@@", 1)
        vid = vid.split("@@")[0].strip()
        path = "/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid
        if mode == "s0":
            return {"parse": 0, "playUrl": "", "url": self.host + path,
                    "header": {"User-Agent": UA, "Referer": self.host + "/"}}
        html = self._get(path, tries=2)
        url = ""
        if html:
            info = self._player_info(html)
            url = (info.get("url") or "").strip()
            if url and not self.isVideoFormat(url):
                url = ""
            if not url:
                mm = RE_M3U8.search(html)
                if mm:
                    url = mm.group(1).replace("\\/", "/")
        hd = {"User-Agent": UA}
        if url:
            if mode == "m" and self.isVideoFormat(url):
                return {"parse": 0, "playUrl": "", "url": url, "header": hd}
            if mode != "m":
                return {"parse": 0, "playUrl": "", "url": url, "header": hd}
        # 兜底：交 App 内建嗅探
        return {"parse": 0, "playUrl": "", "url": self.host + path,
                "header": {"User-Agent": UA, "Referer": self.host + "/"}}

    # ---------------- 本地代理（图片/播放页兜底） ----------------
    def _base_url(self):
        try:
            u = self.getProxyUrl(True)
            if u:
                return u
        except Exception:
            pass
        return ""

    def localProxy(self, param=None):
        try:
            if isinstance(param, str):
                try:
                    param = json.loads(param)
                except Exception:
                    return [404, "text/plain", b"bad param", {}]
            if not isinstance(param, dict):
                return [404, "text/plain", b"bad param", {}]
            url = param.get("url") or ""
            if not url:
                return [404, "text/plain", b"no url", {}]
            if url.startswith("url="):
                url = url[4:]
            if "%" in url:
                try:
                    from urllib.parse import unquote
                    url = unquote(url)
                except Exception:
                    pass
            is_img = param.get("type") == "img" or re.search(r'\.(jpg|jpeg|png|webp)(\?|$)', url, re.I)
            hd = {"User-Agent": UA, "Accept": "*/*",
                  "Referer": self.host + "/", "Accept-Encoding": "gzip, deflate"}
            s = _session()
            if s is None:
                return [404, "text/plain", b"no session", {}]
            r = s.get(url, headers=hd, timeout=(8, 25))
            body = _decomp(r.content, r.headers)
            if not body or body[:1] == b"<":
                return [404, "text/plain", b"fail", {}]
            if is_img:
                mime = {b"\xff\xd8": "image/jpeg", b"\x89PNG": "image/png",
                        b"GIF8": "image/gif"}.get(body[:3], "image/jpeg")
                if body[:4] == b"RIFF":
                    mime = "image/webp"
            else:
                mime = "text/html; charset=utf-8"
            return [200, mime, body, {"Content-Type": mime}]
        except Exception:
            return [404, "text/plain", b"error", {}]