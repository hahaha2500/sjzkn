# -*- coding: utf-8 -*-
# 采集合集 播放修复版 (CjJson_Pro_PlayFix)
# 修复点: 1)yun/share/parse 线路二次解析出真 m3u8  2)解密串走 parse=1
#         3)播放现取现播不缓存 4)localProxy 四元组  5)搜索分页契约

import json, os, re, time, hashlib, base64
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    from requests.adapters import HTTPAdapter
except Exception:
    requests = None
    HTTPAdapter = None

try:
    from base.spider import Spider as _Base
except Exception:
    class _Base(object):
        def getCache(self, k):
            return None
        def setCache(self, k, v, t):
            return None
        def getProxyUrl(self, local=True):
            return ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 播放页里找真流的正则（按命中优先级）
_RE_M3U8 = re.compile(r'["\'\s(](https?:)?//[^"\'\s<>\\)]+?\.m3u8(?:\?[^"\'\s<>\\)]*)?', re.I)
_RE_MP4 = re.compile(r'["\'\s(](https?:)?//[^"\'\s<>\\)]+?\.mp4(?:\?[^"\'\s<>\\)]*)?', re.I)
_RE_VAR = re.compile(r'(?:var\s+)?(?:main|video_url|play_url|playUrl|url|m3u8|src)\s*[:=]\s*["\']([^"\']{6,300})["\']', re.I)
_RE_IFRAME = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
_RE_ENC = re.compile(r'^[A-Za-z0-9_\-]{16,120}$')   # 疑似加密串(Yparser5...)


def _abs(u, base):
    if not u:
        return ""
    u = u.strip().replace("\\/", "/")
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("/"):
        m = re.match(r'(https?://[^/]+)', base or "")
        return (m.group(1) if m else "") + u
    return u


class Spider(_Base):
    def getName(self):
        return "采集合集·可播版"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return any(e in (url or "").lower() for e in
                   (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts"))

    def manualVideoCheck(self):
        return False

    # ---------------- init ----------------
    def __init__(self):
        try:
            _Base.__init__(self)
        except Exception:
            pass
        self.sites = []
        self.session = None
        self.cache_dir = os.path.join(os.path.expanduser("~"), ".lz/cache/")
        self.memory_cache = {}
        self.disk_ttl = 3600

    def _mksession(self):
        if self.session is not None:
            return self.session
        if requests is not None:
            s = requests.Session()
            try:
                ad = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
                s.mount('http://', ad)
                s.mount('https://', ad)
            except Exception:
                pass
            s.headers.update({"User-Agent": UA, "Connection": "keep-alive"})
            s.verify = False
            try:
                import urllib3
                urllib3.disable_warnings()
            except Exception:
                pass
            self.session = s
        return self.session

    def init(self, extend):
        self._mksession()
        default_path = ("https://edgeone.gh-proxy.org/https://raw.githubusercontent.com/"
                        "wliqi495-create/jaychouqq/refs/heads/main/yingshi/py3/cj.json")
        mode = "0"
        json_path = default_path
        if extend:
            e = extend if isinstance(extend, str) else str(extend)
            if "|" in e:
                p = e.split("|")
                json_path = p[0] if p[0] else default_path
                mode = p[1] if len(p) > 1 else "0"
            elif e in ("0", "1", "2"):
                mode = e
            else:
                json_path = e
        self.sites = []
        try:
            if json_path.startswith(("http://", "https://")):
                r = self.session.get(json_path, timeout=15, verify=False)
                if r.status_code == 200:
                    self.sites = self._filter_sites(r.json().get("api_site", []), mode)
            elif os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    self.sites = self._filter_sites(json.load(f).get("api_site", []), mode)
        except Exception as ex:
            print("[采集合集] 配置加载失败: %s" % ex)
        print("[采集合集] 已加载站点: %d" % len(self.sites))

    def _filter_sites(self, sites, mode):
        sites = [s for s in (sites or []) if s.get("api")]
        if mode == "0":
            return sites
        kws = {"AV", "色", "福利", "成人", "18+", "偷拍", "自拍", "淫", "激情", "GAY", "SEX"}

        def adult(n):
            n = (n or "").upper()
            return n.startswith("AV") or any(k in n for k in kws)
        if mode == "1":
            return [s for s in sites if not adult(s.get("name", ""))]
        return [s for s in sites if adult(s.get("name", ""))]

    # ---------------- 缓存 ----------------
    def _get_disk_cache(self, key):
        try:
            k = hashlib.md5(key.encode("utf-8")).hexdigest()
            p = os.path.join(self.cache_dir, k + ".json")
            if os.path.exists(p):
                if time.time() - os.path.getmtime(p) < self.disk_ttl:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                else:
                    os.remove(p)
        except Exception:
            pass
        return None

    def _set_disk_cache(self, key, data):
        try:
            if not data or not data.get("list"):
                return
            os.makedirs(self.cache_dir, exist_ok=True)
            k = hashlib.md5(key.encode("utf-8")).hexdigest()
            with open(os.path.join(self.cache_dir, k + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _mem_get(self, k):
        v = self.memory_cache.get(k)
        if v and time.time() - v[0] < 300:
            return v[1]
        return None

    def _mem_put(self, k, v):
        if len(self.memory_cache) > 300:
            self.memory_cache.clear()
        self.memory_cache[k] = (time.time(), v)

    # ---------------- 请求 ----------------
    def _fetch(self, api_url, params=None, timeout=4.0):
        try:
            sep = "&" if "?" in api_url else "?"
            qs = "&".join("%s=%s" % (k, v) for k, v in params.items()) if params else ""
            url = api_url + sep + qs if qs else api_url
            res = self.session.get(url, timeout=timeout, verify=False)
            if res.status_code == 200:
                try:
                    return res.json()
                except Exception:
                    return json.loads(res.text.strip().lstrip("\ufeff"))
        except Exception:
            pass
        return {}

    def _get_text(self, url, referer=None, timeout=8):
        try:
            h = {"User-Agent": UA, "Accept": "*/*"}
            if referer:
                h["Referer"] = referer
            r = self.session.get(url, timeout=timeout, verify=False, headers=h)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
        except Exception:
            pass
        return ""

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        classes, filters = [], {}
        uf = [{"key": "cateId", "name": "分类", "value": [
            {"n": "全部", "v": ""}, {"n": "动作片", "v": "动作"}, {"n": "喜剧片", "v": "喜剧"},
            {"n": "爱情片", "v": "爱情"}, {"n": "科幻片", "v": "科幻"}, {"n": "恐怖片", "v": "恐怖"},
            {"n": "剧情片", "v": "剧情"}, {"n": "战争片", "v": "战争"}, {"n": "国产剧", "v": "国产"},
            {"n": "港剧", "v": "香港"}, {"n": "韩剧", "v": "韩国"}, {"n": "欧美剧", "v": "欧美"},
            {"n": "台剧", "v": "台湾"}, {"n": "日剧", "v": "日本"}, {"n": "纪录片", "v": "记录"},
            {"n": "动漫", "v": "动漫"}, {"n": "综艺", "v": "综艺"}]}]
        for i, s in enumerate(self.sites):
            n = (s.get("name") or ("站点%d" % i)).replace("TV-", "").replace("AV-", "")
            classes.append({"type_id": str(i), "type_name": n})
            filters[str(i)] = uf
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        return {"list": []}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, ext):
        cate = ""
        if isinstance(ext, dict):
            cate = ext.get("cateId", "") or ""
        elif isinstance(ext, str) and ext:
            try:
                cate = json.loads(ext).get("cateId", "") or ""
            except Exception:
                cate = ""
        key = "CAT_%s_%s_%s" % (tid, pg, cate)
        cached = self._get_disk_cache(key)
        if cached:
            return cached
        try:
            idx = int(tid)
            if idx >= len(self.sites):
                return self._empty()
        except Exception:
            return self._empty()
        site = self.sites[idx]
        pcs = set(x for x in str(site.get("paichu", "")).split(",") if x)
        data = self._fetch(site["api"], {"ac": "detail", "pg": pg}, timeout=6)
        vl = []
        for it in (data.get("list") or []):
            if pcs and str(it.get("type_id")) in pcs:
                continue
            if cate and cate not in str(it.get("type_name", "")):
                continue
            it["vod_id"] = "%d@@%s" % (idx, it["vod_id"])
            it["vod_remarks"] = it.get("vod_remarks") or it.get("type_name") or ""
            vl.append(it)
        res = {
            "page": _i(data.get("page", pg), pg),
            "pagecount": _i(data.get("pagecount", 1), 1),
            "limit": _i(data.get("limit", 20), 20),
            "total": _i(data.get("total", len(vl)), len(vl)),
            "list": vl,
        }
        self._set_disk_cache(key, res)
        return res

    def _empty(self):
        return {"page": 1, "pagecount": 1, "limit": 20, "total": 0, "list": []}

    # ---------------- 详情 ----------------
    def detailContent(self, array):
        if not array:
            return {"list": []}
        full = str(array[0])
        m = self._mem_get(full)
        if m:
            return m
        if "@@" not in full:
            return {"list": []}
        try:
            idx, vid = full.split("@@", 1)
            idx = int(idx)
            site = self.sites[idx]
            data = self._fetch(site["api"], {"ac": "detail", "ids": vid}, timeout=6)
            if data.get("list"):
                it = dict(data["list"][0])
                it["vod_id"] = full
                res = {"list": [it]}
                self._mem_put(full, res)
                return res
        except Exception:
            pass
        return {"list": []}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        if not key:
            return self._empty()
        pg = str(pg or "1")
        ckey = "SEARCH_%s_%s" % (key, pg)
        cached = self._get_disk_cache(ckey)
        if cached:
            return cached
        targets = []
        for i, s in enumerate(self.sites):
            if str(s.get("bz", "1")).strip() != "0" and s.get("api"):
                targets.append((i, s))

        def one(t):
            idx, s = t
            try:
                pcs = set(x for x in str(s.get("paichu", "")).split(",") if x)
                d = self._fetch(s["api"], {"ac": "detail", "wd": key, "pg": pg}, timeout=3.0)
                nm = (s.get("name") or "").replace("TV-", "").replace("AV-", "")
                out = []
                for it in (d.get("list") or []):
                    if pcs and str(it.get("type_id")) in pcs:
                        continue
                    it["vod_id"] = "%d@@%s" % (idx, it["vod_id"])
                    it["vod_name"] = "[%s] %s" % (nm, it.get("vod_name", ""))
                    out.append(it)
                return out
            except Exception:
                return []

        final = []
        with ThreadPoolExecutor(max_workers=30) as ex:
            futs = [ex.submit(one, t) for t in targets]
            res = []
            for f in as_completed(futs):
                try:
                    r = f.result()
                    if r:
                        res.append(r)
                except Exception:
                    pass
        for r in res:
            final.extend(r)
        out = {"page": int(pg), "pagecount": int(pg), "limit": 20,
               "total": len(final), "list": final}
        self._set_disk_cache(ckey, out)
        return out

    # ---------------- 播放（核心修复） ----------------
    def playerContent(self, flag, id, vipFlags):
        play = str(id or "")
        if not play:
            return {"url": "", "header": {"User-Agent": UA}, "parse": 0, "jx": 0}
        direct = self._direct(play)
        if direct:
            return self._r0(direct, play)
        # 疑似加密串 / 需要解析器
        if _RE_ENC.match(play) and "." not in play and "/" not in play:
            return self._r1(play)
        # yun / share / play 页面 -> 二次解析
        real = self._resolve(play)
        if real:
            return self._r0(real, play)
        return self._r0(play)

    def _r0(self, url, ref=""):
        h = {"User-Agent": UA}
        if ref and ref.startswith("http"):
            h["Referer"] = self._ref_for(ref)
        return {"url": url, "header": h, "parse": 0, "jx": 0}

    def _r1(self, url):
        return {"url": url, "header": {"User-Agent": UA}, "parse": 1, "jx": 0}

    def _ref_for(self, url):
        m = re.match(r'(https?://[^/]+)', url)
        return (m.group(1) + "/") if m else url

    def _direct(self, u):
        if u.startswith(("http://", "https://")) and re.search(r'\.(m3u8|mp4|flv|ts)(\?|$)', u, re.I):
            return u
        return None

    def _resolve(self, page, depth=0):
        """页面 -> 真 m3u8（多正则 + iframe 二次，最多 2 层）"""
        html = self._get_text(page, referer=self._ref_for(page), timeout=8)
        if not html:
            return None
        for rx in (_RE_M3U8, _RE_MP4):
            for m in rx.findall(html):
                u = _abs(m if isinstance(m, str) else m[0], page)
                if u and ".m3u8" in u or (u and ".mp4" in u):
                    if re.search(r'\.(m3u8|mp4)(\?|$)', u, re.I):
                        return u
        for m in _RE_VAR.findall(html):
            u = _abs(m, page)
            if re.search(r'\.(m3u8|mp4)(\?|$)', u, re.I):
                return u
        if depth < 1:
            for m in _RE_IFRAME.findall(html)[:3]:
                u = _abs(m, page)
                if u.startswith("http") and u != page:
                    r = self._resolve(u, depth + 1)
                    if r:
                        return r
            rel = self._rel_cand(html, page)
            if rel:
                return rel
            # 新浪/极速/金鹰类：/play/xxxx  ->  /play/xxxx/index.m3u8
            if re.match(r'^https?://[^/]+/play/[A-Za-z0-9_\-]+/?$', page):
                u = page.rstrip("/") + "/index.m3u8"
                try:
                    rr = self.session.get(u, timeout=8, verify=False,
                                         headers={"User-Agent": UA,
                                                  "Referer": self._ref_for(u)})
                    if rr.status_code == 200 and "#EXTM3U" in rr.text:
                        return u
                except Exception:
                    pass
        return None

    def _rel_cand(self, html, page):
        """页面里的根相对 m3u8（实测新浪/极速/金鹰等都是 /play/xxx/index.m3u8）"""
        for m in re.findall(r'["\'\s(=]((?:/[^\s"\'<>\\)]+\.m3u8)(?:\?[^\s"\'<>\\)]*)?)', html)[:8]:
            u = _abs(m, page)
            if u.startswith("http"):
                return u
        return None

    # ---------------- 本地代理 ----------------
    def localProxy(self, params=None):
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        if not isinstance(params, dict):
            params = {}
        url = params.get("url") or params.get("u") or ""
        if not url:
            return [404, "text/plain", b"", {"Content-Type": "text/plain"}]
        try:
            r = self.session.get(url, timeout=15, verify=False,
                                 headers={"User-Agent": UA,
                                          "Referer": self._ref_for(url)})
            body = r.content
            hd = {"Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
                  "User-Agent": UA}
            return [r.status_code, hd["Content-Type"], body, hd]
        except Exception:
            return [404, "text/plain", b"", {"Content-Type": "text/plain"}]


def _i(v, d):
    try:
        return int(v)
    except Exception:
        return d