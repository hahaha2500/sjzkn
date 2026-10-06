# -*- coding: utf-8 -*-
# 橙果短剧 chengguodj.com  TVBox/FongMi type=3 Python
# 站型: Nuxt SSR, 数据在 __NUXT_DATA__ (nuxt uneval 扁平数组)
import json
import re
import threading
import time
import gzip
import zlib
import urllib.request
import urllib.parse as _up
import random
from concurrent.futures import ThreadPoolExecutor

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self):
            pass

HOST = "https://chengguodj.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HDR = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# 分类: name, tid(路由段), filters
CATS = [
    ("推荐", "aiduanju"),
    ("AI短剧", "aiduanju"),
    ("真人短剧", "zhenren"),
    ("漫剧", "manju"),
    ("原创", "yuanchuang"),
    ("全部", "browse"),
]
CHMAP = {"aiduanju": "AI短剧", "zhenren": "真人", "manju": "漫剧",
         "yuanchuang": "原创", "mogai": "魔改"}

_CACHE = {}
_CLOCK = time.time
_LOCK = threading.Lock()
TIMEOUT = 15


def _decomp(raw, enc):
    if enc == "gzip":
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    elif enc == "deflate":
        for f in (lambda b: zlib.decompress(b), lambda b: zlib.decompress(b, -zlib.MAX_WBITS)):
            try:
                return f(raw)
            except Exception:
                pass
    return raw


def _http(url, ref=None, tries=6):
    h = dict(HDR)
    if ref:
        h["Referer"] = ref
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            rq = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = rq.read()
            raw = _decomp(raw, (rq.headers.get("Content-Encoding") or "").lower())
            return raw.decode("utf-8", "ignore")
        except Exception as e:
            last = "%s" % e
            if i + 1 < tries:
                time.sleep(min(1.2 * (i + 1) + random.random() * 0.5, 5))
    print("[橙果] 请求失败 %s -> %s" % (url, last))
    return ""


def _cached(key, url, ttl=300, ref=None):
    with _LOCK:
        it = _CACHE.get(key)
        if it and _CLOCK() - it[0] < ttl:
            return it[1]
    html = _http(url, ref)
    if not html:
        return ""
    with _LOCK:
        if len(_CACHE) > 400:
            _CACHE.clear()
        _CACHE[key] = (_CLOCK(), html)
    return html


def _uneval(html):
    """还原 Nuxt __NUXT_DATA__ 扁平数组为嵌套 dict/list。"""
    i = html.find("__NUXT_DATA__")
    if i < 0:
        return None
    s = html.find(">", i)
    e = html.find("</script>", s)
    if s < 0 or e < 0:
        return None
    try:
        arr = json.loads(html[s + 1:e])
    except Exception:
        return None
    try:
        return _rev(arr, 1, {})
    except Exception:
        return None


# devalue 扁平数组约定: 下标 0 = false, -1 = null, 其余为真实槽位;
# arr[0] 本身是类型标记(根), 不能当数据槽, 故根固定从下标 1 起。
def _rev(arr, idx, memo):
    if idx == 0:
        return False
    if idx == -1:
        return None
    if idx in memo:
        return memo[idx]
    v = arr[idx] if 0 <= idx < len(arr) else None
    if isinstance(v, bool) or v is None or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, list):
        if len(v) == 2 and isinstance(v[0], str) and \
                v[0] in ("ShallowReactive", "Reactive", "Ref", "ShallowRef",
                          "Shallow", "ShallowReactiveMap", "RefNuxt"):
            return _rev(arr, v[1], memo)
        out = []
        memo[idx] = out
        for x in v:
            out.append(_rev(arr, x, memo) if isinstance(x, int) and not isinstance(x, bool) else x)
        return out
    if isinstance(v, dict):
        out = {}
        memo[idx] = out
        for k, x in v.items():
            out[k] = _rev(arr, x, memo) if isinstance(x, int) and not isinstance(x, bool) else x
        return out
    return v


def _payload(html):
    """Nuxt payload: root.data[hash].data 取业务数据，逐层兜底。"""
    d = _uneval(html)
    if not isinstance(d, dict):
        return {}
    node = d
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        if any(k in node for k in ("dramas", "hot", "drama", "episodes", "results")):
            return node
        nxt = None
        for k in ("data", "props", "state", "status"):
            v = node.get(k)
            if isinstance(v, dict):
                nxt = v
                break
        if nxt is None:
            for v in node.values():
                if isinstance(v, dict):
                    nxt = v
                    break
        if nxt is None or nxt is node:
            return node
        node = nxt
    return node if isinstance(node, dict) else {}


def _clean(t):
    if not isinstance(t, str):
        return ""
    return re.sub(r"\s+", " ", t.replace("$", "").replace("#", "")).strip()


def _chn_prefix(chn):
    if isinstance(chn, dict):
        chn = chn.get("code") or chn.get("name") or ""
    if not isinstance(chn, str):
        return ""
    return (CHMAP.get(chn, "") + "·") if chn in CHMAP else ""


def _pic_of(v):
    """cover 可能是 str 或 {url, fallback_url} 结构。"""
    if isinstance(v, dict):
        u = v.get("url") or ""
        return u if isinstance(u, str) else ""
    return v if isinstance(v, str) else ""


def _bad_pic(u):
    if not u or not u.startswith("http"):
        return True
    low = u.lower()
    for w in ("/static/posters/", "default", "placeholder", "loading"):
        if w in low:
            return True
    return False


class Spider(BaseSpider):

    def __init__(self):
        BaseSpider.__init__(self)
        self.host = HOST
        try:
            ex = self.getConf()
        except Exception:
            ex = None
        self.extend = ex if isinstance(ex, dict) else {}

    # ---------- 基础 ----------
    def getName(self):
        return "橙果短剧"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return any(e in (url or "").lower() for e in
                   [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        ex = self.extend
        if isinstance(extend, str) and extend.strip():
            try:
                d = json.loads(extend)
                if isinstance(d, dict):
                    ex = d
            except Exception:
                if extend.strip().startswith("http"):
                    ex = {"host": extend.strip().rstrip("/")}
        if isinstance(ex, dict) and ex.get("host"):
            self.host = str(ex["host"]).rstrip("/")

    def homeContent(self, filter=False):
        classes = [{"type_id": t, "type_name": n} for n, t in CATS]
        return {"class": classes}

    def homeVideoContent(self):
        html = _cached("home", self.host + "/aiduanju", 180, self.host + "/")
        vods = self._cards(html)
        return {"list": vods[:60]}

    # ---------- 列表 ----------
    def _cards(self, html):
        p = _payload(html)
        if not p:
            return []
        lst = []
        for k in ("dramas", "hot", "results", "items", "list"):
            v = p.get(k)
            if isinstance(v, list) and v:
                lst = v
                break
        out = []
        seen = set()
        for d in lst:
            if not isinstance(d, dict):
                continue
            rid = d.get("slug") or d.get("id")
            if not rid or rid in seen:
                continue
            name = _clean(d.get("title"))
            if not name:
                continue
            seen.add(rid)
            pic = _pic_of(d.get("cover"))
            if _bad_pic(pic):
                fb = _pic_of(d.get("cover_fallback_url"))
                pic = fb if not _bad_pic(fb) else ""
            ep = d.get("total_episodes") or d.get("total_episode_count") or 0
            if not isinstance(ep, int):
                ep = 0
            name = "%s%s" % (_chn_prefix(d.get("channel")), name)
            out.append({"vod_id": str(rid), "vod_name": name, "vod_pic": pic,
                        "vod_remarks": "%s集" % ep if ep else ""})
        return out

    def categoryContent(self, tid, pg, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        tid = (tid or "aiduanju").strip()
        if tid.startswith("http"):
            tid = tid.rstrip("/").split("/")[-1] or "aiduanju"
        if tid.isdigit():
            for n, t in CATS:
                if t == tid:
                    tid = t
                    break
        path = "/" + tid if pg <= 1 else "/%s/page-%d" % (tid, pg)
        html = _cached("cat:%s:%d" % (tid, pg), self.host + path, 300, self.host + "/")
        vods = self._cards(html)
        pc = self._pagecount(html, pg)
        return {"list": vods, "page": pg, "pagecount": pc, "limit": 30, "total": len(vods)}

    def _pagecount(self, html, pg):
        try:
            p = _payload(html)
            pag = p.get("pagination") if isinstance(p, dict) else None
            if isinstance(pag, dict):
                v = int(pag.get("total_pages") or 0)
                if v > 0:
                    return v
        except Exception:
            pass
        pc = 0
        for m in re.finditer(r"/page-(\d+)", html or ""):
            try:
                pc = max(pc, int(m.group(1)))
            except Exception:
                pass
        return pc if pc > 0 else pg

    # ---------- 搜索 ----------
    def searchContent(self, key, quick="", pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if not key:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 30, "total": 0}
        
        kw = _up.quote(key, safe="")
        url = "%s/search/%s" % (self.host, kw) if pg <= 1 else \
            "%s/search/%s/page-%d" % (self.host, kw, pg)
        html = _cached("se:%s:%d" % (key, pg), url, 180, self.host + "/")
        out = self._cards(html)
        return {"list": out, "page": pg, "pagecount": self._pagecount(html, pg),
                "limit": 30, "total": len(out)}
        lst = []
        seen = set()
        for d in lst:
            if not isinstance(d, dict):
                continue
            rid = d.get("slug") or d.get("id")
            if not rid or rid in seen:
                continue
            name = _clean(d.get("title"))
            if not name:
                continue
            seen.add(rid)
            pic = _pic_of(d.get("cover"))
            if _bad_pic(pic):
                fb = _pic_of(d.get("cover_fallback_url"))
                pic = fb if not _bad_pic(fb) else ""
            ep = d.get("total_episodes") or 0
            if not isinstance(ep, int):
                ep = 0
            out.append({"vod_id": str(rid), "vod_name": name, "vod_pic": pic,
                        "vod_remarks": "%s集" % ep if ep else ""})
        return {"list": out, "page": pg, "pagecount": self._pagecount(html, pg),
                "limit": 30, "total": len(out)}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        vid = ""
        if isinstance(ids, list) and ids:
            vid = str(ids[0])
        elif ids:
            vid = str(ids)
        vid = vid.split("@@")[-1] or vid
        if not vid:
            return {}
        html = _cached("dt:%s" % vid, self.host + "/drama/" + vid, 600, self.host + "/")
        p = _payload(html)
        d = p.get("drama") if isinstance(p.get("drama"), dict) else p
        if not isinstance(d, dict):
            d = {}
        eps = []
        if isinstance(p, dict):
            for k in ("episodes", "episode_list", "list"):
                if isinstance(p.get(k), list):
                    eps = p[k]
                    break
        segs = []
        titles = []
        for e in eps:
            if not isinstance(e, dict):
                continue
            num = e.get("number") or 0
            nm = _clean(e.get("title")) or "第%s集" % num
            try:
                num = int(num)
            except Exception:
                num = len(segs) + 1
            if nm == _clean(str(num)) or nm == str(num):
                nm = "第%d集" % num
            segs.append("%s$%s@@%d" % (nm, vid, num))
            titles.append(num)
        if not segs:
            segs.append("正片$%s@@1" % vid)
        name = _clean(d.get("title")) or vid
        pic = _pic_of(d.get("cover"))
        if _bad_pic(pic):
            fb = _pic_of(d.get("cover_fallback_url"))
            pic = fb if not _bad_pic(fb) else ""
        chn = d.get("channel")
        if isinstance(chn, dict):
            chn = chn.get("name") or chn.get("code") or ""
        if not isinstance(chn, str):
            chn = ""
        return {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_year": str(d.get("published_at") or "")[:4],
            "vod_area": chn,
            "vod_remarks": "%s集" % (d.get("total_episode_count") or len(segs)),
            "vod_actor": "",
            "vod_director": "",
            "vod_content": _clean(d.get("intro")),
            "vod_play_from": "橙果·主线$$$橙果·原页",
            "vod_play_url": "%s#%s" % ("#".join(segs), "#".join(segs)),
        }

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags=None):
        parts = [x for x in str(id).split("@@") if x]
        vid = parts[0] if parts else ""
        num = 1
        if len(parts) >= 2:
            try:
                num = int(parts[-1])
            except Exception:
                num = 1
        if not vid:
            return {"parse": 0, "url": ""}
        page = "%s/play/%s/%d" % (self.host, vid, num)
        if "原页" in str(flag or ""):
            return {"parse": 0, "url": page, "header": {"User-Agent": UA,
                                                         "Referer": self.host + "/"}}
        url = self._m3u8(page)
        if url:
            return {"parse": 0, "url": url, "header": {"User-Agent": UA,
                                                        "Referer": self.host + "/"}}
        return {"parse": 0, "url": page, "header": {"User-Agent": UA,
                                                     "Referer": self.host + "/"}}

    def _m3u8(self, page):
        """播放页 SSR 数据里抠真实流地址(绝对 m3u8/mp4, 带 auth_key)。"""
        html = _http(page, self.host + "/")
        if not html:
            return ""
        html = html.replace("\\u002F", "/").replace("\\/", "/")
        for key in ("source_url", "h265_url", "mp4_url", "play_url", "url"):
            m = re.search(r'"%s"\s*:\s*"(https?://[^"]+)"' % key, html)
            if m and (".m3u8" in m.group(1) or ".mp4" in m.group(1)):
                return m.group(1)
        m = re.search(r'https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+?\.m3u8[^"\\\s<]*', html)
        return m.group(0) if m else ""

    # ---------- 本地代理 ----------
    def localProxy(self, param=None):
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except Exception:
                return [404, "text/plain", b"", {}]
        if not isinstance(param, dict):
            return [404, "text/plain", b"", {}]
        url = param.get("url") or ""
        if not url:
            return [404, "text/plain", b"", {}]
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": self.host + "/"})
        try:
            rq = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = _decomp(rq.read(), (rq.headers.get("Content-Encoding") or "").lower())
            mime = rq.headers.get("Content-Type") or "application/octet-stream"
        except Exception:
            return [404, "text/plain", b"", {}]
        if b"<" == raw[:1] or raw[:5] == b"<!doc":
            return [404, "text/plain", b"", {}]
        return [200, mime, raw, {"Content-Type": mime}]
