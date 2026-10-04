# -*- coding: utf-8 -*-
"""
xmldc.py —— 小马拉大车（xmldc12.wiki）TVBox/FongMi 爬虫（type=3 Python）

站点逆向要点（2026-10-04 实测，andro 交 android 与用户侧共用同一份结论）：
  · 站型：MacCMS v10 + 016_tpl_wap 老模板，/api.php/provide/vod 已关（404）→ 纯 HTML 直抓
  · ★入口路径 /xmldc/ 是台群别名路径；站内真实路由一律在 /cn/home/web/index.php/vod/ 下
     （常规 /index.php/vod/ 直接 404）—— 基址必须用域名根，不能拿入口路径当 base
  · 列表：/cn/home/web/index.php/vod/type/id/{tid}.html
          分页 /cn/home/web/index.php/vod/type/id/{tid}/page/{pg}.html（第2页起）
          尾页从 /page/{N}.html 抠（如 tid=20 → 2071 页）
  · ★没有详情页：列表卡直接就是播放页 /vod/play/id/{vid}/sid/1/nid/1.html（单集单线路）
  · 播放页 var player_data={"encrypt":0,...,"url":"明文m3u8"} → encrypt=0 直出 parse:0
  · 搜索：/cn/home/web/index.php/vod/search.html?wd={quote}
     ★分页参数无效（?page= 与 /search/page/N.html 都返回同一份内容）→ 诚实 pagecount=1
  · 分类 tid 20~29 共 10 个（绝美少女/激情口交/亚洲日韩/人妖激情/重咸口味/
     国产专区/日韩专区/欧美专区/卡通动漫/三级伦理），另有兜底写死
  · 海报：img.loading 占位图 + data-original 真图；图床多域（fqjpg11.top / 2608.xbpi2608.top /
     fh260908.top / jpxjpg9.top / sycdn.pic-726-baidu.com …），取图多字段兜底

加载层守默影视(webhtv)铁律：
  getDependence()→[]、显式 __init__ + 父类、localProxy(param=None) 四元组、header 全 dict、
  代理地址带 siteKey=<源key>、端口 9978~9999 动态不写死、六接口 return dict 不 dumps。
"""

import base64
import json
import os
import random
import re
import threading
import time
import urllib.parse

try:
    from base.spider import Spider as BaseSpider          # T4 / FongMi Chaquopy
except Exception:                                          # 裸 class（默影视/webhtv）
    class BaseSpider(object):
        def __init__(self):
            pass

try:
    requests = __import__("requests")
except Exception:
    requests = None

from urllib.request import Request as _URequest, urlopen as _urlopen, ProxyHandler as _ProxyHandler, build_opener as _build_opener
from urllib.error import HTTPError as _HTTPError

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

HOST = "https://www.xmldc12.wiki"
ROUTE = "/cn/home/web/index.php/vod"          # 站内唯一真实路由前缀

FALLBACK_CLASS = [
    ("20", "绝美少女"), ("21", "激情口交"), ("22", "亚洲日韩"), ("23", "人妖激情"),
    ("24", "重咸口味"), ("25", "国产专区"), ("26", "日韩专区"), ("27", "欧美专区"),
    ("28", "卡通动漫"), ("29", "三级伦理"),
]

BAD_IMG = ("loading", "logo", "placeholder", "blank", "220x307", "1x1", ".gif")
BLOCK_TID = ()

_CACHE = {}
_CACHE_TTL = 300
_SESSION = {}
_LOCK = threading.Lock()
# 壳可能每次调用都 new Spider，故 vid->片名/封面 必须放模块级
_PICS = {}

RE_VIDEO = re.compile(r'/vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)\.html')
# 抓整段开标签，title 另行提取（★别把 title 写成可选组：前导 [^>]* 会把它整组跳过）
RE_ATAG = re.compile(r'<a\b([^>]*?href="[^"]*/vod/play/id/(\d+)/sid/\d+/nid/\d+\.html"[^>]*)>')
RE_DATAORIG = re.compile(r'data-original="([^"]+)"')
RE_DATAORIG2 = re.compile(r'data-src="([^"]+)"')
RE_SRC = re.compile(r'src="(https?://[^"]+)"')
RE_TITLE2 = re.compile(r'<label class="name">([^<]{1,200})</label>')
RE_PAGELAST = re.compile(r'/page/(\d+)\.html')
RE_PLAYER = re.compile(r'player_data\s*=\s*(\{.*?\})\s*;?\s*</script>', re.S)
RE_JSONURL = re.compile(r'"url"\s*:\s*"([^"]+)"')
RE_CODE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,6})\s*[-_ ]?\s*(\d{3,5})(?!\d)')
CODE_BAD = ("id", "mv", "hd", "tv", "3d", "4k", "uk", "us", "jp", "cn", "xx", "ss", "av", "www")

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class Spider(BaseSpider):
    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = HOST
        self.ext = {}
        self.img_mode = "1"
        self.site_key = ""

    # ---------------- 加载层 ----------------
    def getName(self):
        return "小马拉大车"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        low = (url or "").lower().split("?")[0]
        return any(ext in low for ext in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"))

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        if extend:
            if isinstance(extend, dict):
                self.ext = extend
            else:
                s = str(extend).strip()
                if s.startswith("{"):
                    try:
                        self.ext = json.loads(s)
                    except Exception:
                        self.ext = {"host": s}
                elif s.startswith("http"):
                    self.ext = {"host": s}
                else:
                    try:
                        self.ext = {"siteKey": s}
                    except Exception:
                        self.ext = {}
        if self.ext.get("host"):
            self.host = str(self.ext["host"]).rstrip("/")
        self.img_mode = str(self.ext.get("img_mode", "1"))
        self.site_key = str(self.ext.get("siteKey", "") or "")

    # ---------------- 网络 ----------------
    def _sess(self):
        k = threading.current_thread().name
        s = _SESSION.get(k)
        if s is None:
            s = {}
            if requests is not None:
                s = requests.Session()
                try:
                    ad = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=40)
                    s.mount("http://", ad)
                    s.mount("https://", ad)
                except Exception:
                    pass
            _SESSION[k] = s
        return s

    def _get(self, path, referer=None, timeout=18, raw=False):
        """单入口：拼接 base + 站内路由，返回 text 或 bytes；失败返回 None"""
        url = path if path.startswith("http") else (self.host + path)
        key = url
        with _LOCK:
            hit = _CACHE.get(key)
        if hit and (time.time() - hit[0]) < _CACHE_TTL:
            return hit[1]
        body = None
        ua = random.choice(UA_POOL)
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        try:
            if requests is not None:
                r = self._sess().get(url, headers=headers, timeout=timeout)
                if r.status_code == 200:
                    body = r.content if raw else r.text
                else:
                    body = None
            else:
                req = _URequest(url, headers=headers)
                resp = _urlopen(req, timeout=timeout)
                body = resp.read() if raw else resp.read().decode("utf-8", "ignore")
        except _HTTPError as e:
            body = None
        except Exception:
            body = None
        if body is not None:
            with _LOCK:
                if len(_CACHE) > 400:
                    _CACHE.clear()
                _CACHE[key] = (time.time(), body)
        return body

    def _abs(self, u):
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u
        return self.host + u

    # ---------------- 图片 ----------------
    def _clean(self, s):
        return re.sub(r'[\s$#<>"\']', "", (s or "")).strip()

    def _pick_pic(self, block, vid="", whole=""):
        m = RE_DATAORIG.search(block) or RE_DATAORIG2.search(block)
        if m:
            return self._abs(m.group(1))
        # ★推荐块（class pLinks）里的同名视频没有图，会抢占 id：向后回查真卡片的图
        if vid and whole:
            pos = whole.find("/vod/play/id/%s/" % vid)
            while pos > 0:
                seg = whole[pos:pos + 2600]
                m2 = RE_DATAORIG.search(seg) or RE_DATAORIG2.search(seg)
                if m2:
                    return self._abs(m2.group(1))
                nxt = whole.find("/vod/play/id/", pos + 10)
                if nxt < 0 or nxt - pos > 2600:
                    break
                pos = nxt
        m = RE_SRC.search(block)
        return self._abs(m.group(1)) if m else ""

    def _dmm(self, title):
        """番号拼 DMM 封面兜底（我环境被墙无法验证，故仅在原图完全取不到时启用）"""
        m = RE_CODE.search(title or "")
        if not m:
            return ""
        L, N = m.group(1), m.group(2)
        if L.lower() in CODE_BAD:
            return ""
        P = L.lower() + str(int(N)).zfill(5)
        return "http://pics.dmm.co.jp/digital/video/%s/%spl.jpg" % (P, P)

    def _img_via(self, url):
        """本地代理三档 Referer 降级取图；全败回 1x1 合法 PNG（宁小图也不留白块）"""
        if not url.startswith("http"):
            return ""
        base = self._proxy_base()
        if not base:
            # ★没有本地代理端口（壳未起服务）时必须回落原图直连，绝不能返空串
            return url
        host = re.match(r'https?://([^/]+)/', url)
        host = host.group(1) if host else ""
        trials = [
            {},                                                          # 无 Referer（多数图床放行）
            {"Referer": "https://" + host + "/"},                       # 图床自身域
            {"Referer": self.host + "/", "User-Agent": UA_POOL[0]},     # 站点 + 普通浏览器 UA
        ]
        for extra in trials:
            p = {"url": url, "type": "img"}
            hdr = {"User-Agent": random.choice(UA_POOL), "Accept": "image/*,*/*"}
            hdr.update(extra)
            try:
                if requests is not None:
                    r = self._sess().get(url, headers=hdr, timeout=12)
                    if r.status_code == 200 and r.content and r.content[:1] == b"\xff" \
                            or (r.status_code == 200 and r.content[:1] == b"\x89"):
                        return self._proxy_url_for(url, True)
                else:
                    resp = _urlopen(_URequest(url, headers=hdr), timeout=12)
                    b = resp.read(64)
                    if b[:1] in (b"\xff", b"\x89", b"\x47", b"\x52"):
                        return self._proxy_url_for(url, True)
            except Exception:
                continue
        return self._proxy_url_for(url, True)      # 交壳子走代理再试，兜底也不给空

    def _pic(self, title, raw=""):
        if raw and not any(b in raw.lower() for b in BAD_IMG):
            if self.img_mode == "1":
                v = self._img_via(raw)
                if v:
                    return v
            return raw
        d = self._dmm(title)
        if not d:
            return ""
        if self.img_mode == "1":
            v = self._img_via(d)
            if v:
                return v
        return d

    def _img_bytes(self, url):
        try:
            if requests is not None:
                r = self._sess().get(url, headers={"User-Agent": random.choice(UA_POOL)}, timeout=15)
                if r.status_code == 200 and r.content:
                    return r.content
            else:
                req = _URequest(url, headers={"User-Agent": random.choice(UA_POOL)})
                resp = _urlopen(req, timeout=15)
                b = resp.read()
                if b:
                    return b
        except Exception:
            return None
        return None

    # ---------------- 本地代理（默影视 ?do=py） ----------------
    def _proxy_base(self):
        base = ""
        if requests is not None:
            try:
                base = self.getProxyUrl(True)
            except Exception:
                base = ""
        if not base:
            try:
                from com.github.catvod import Proxy
                base = Proxy.getUrl(True)
            except Exception:
                base = ""
        if not base:
            for p in range(9978, 9999):
                cand = "http://127.0.0.1:%d/proxy?do=py" % p
                if self._probe(cand):
                    base = cand
                    break
        if base and "siteKey=" not in base:
            sk = self.site_key or self.getName()
            base = base + ("&" if "?" in base else "?") + "siteKey=" + urllib.parse.quote(sk)
        return base

    def _probe(self, url):
        try:
            if requests is not None:
                self._sess().get(url, timeout=3)
            else:
                _urlopen(_URequest(url), timeout=3)
            return True
        except Exception:
            return False

    def localProxy(self, param=None):
        p = param
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {"url": p}
        if not isinstance(p, dict):
            return [404, "text/plain", b"", {}]
        url = p.get("url", "")
        if not url.startswith("http"):
            return [404, "text/plain", b"", {}]
        is_img = p.get("type") == "img"
        hdr = {"User-Agent": random.choice(UA_POOL), "Accept": "*/*"}
        if is_img:
            hdr["Referer"] = re.sub(r"(/[^/]+)?$", "/", url)
        try:
            if requests is not None:
                r = self._sess().get(url, headers=hdr, timeout=20)
                if r.status_code != 200 or not r.content:
                    return [404, "text/plain", b"", hdr]
                body = r.content
                mime = r.headers.get("Content-Type", "image/jpeg")
            else:
                req = _URequest(url, headers=hdr)
                resp = _urlopen(req, timeout=20)
                body = resp.read()
                mime = resp.headers.get("Content-Type", "image/jpeg")
        except Exception:
            return [404, "text/plain", b"", hdr]
        if not body:
            body = _PNG_1x1
            mime = "image/png"
        return [200, mime.split(";")[0], body, hdr]

    def _proxy_url_for(self, url, is_img=False):
        base = self._proxy_base()
        if not base:
            return url
        b64 = base64.b64encode(url.encode("utf-8")).decode("ascii")
        sep = "&" if "?" in base else "?"
        if is_img:
            return base + sep + "type=img&url=" + urllib.parse.quote(b64, safe="")
        return base + sep + "url=" + urllib.parse.quote(b64, safe="")

    # ---------------- 解析 ----------------
    def _nav(self):
        html = self._get(ROUTE.replace("/index.php/vod", "") + "/../index.php/vod/type/id/20.html") or ""
        t = self._get("/cn/home/web/index.php/vod/type/id/20.html") or html
        out = []
        for tid, name in re.findall(r'/vod/type/id/(\d+)\.html"[^>]*>\s*([^<]{1,20})', t):
            n = self._clean(name)
            if n and (tid, n) not in out:
                out.append((tid, n))
        home = self._get("/xmldc/") or ""
        for tid, name in re.findall(r'/vod/type/id/(\d+)\.html"[^>]*>\s*([^<]{1,20})', home):
            n = self._clean(name)
            if n and (tid, n) not in out:
                out.append((tid, n))
        return out or list(FALLBACK_CLASS)

    def _parse_list(self, html, strict=True):
        items = []
        seen = set()
        if not html:
            return items
        # 切分：按 a 开标签定位每个视频块，逐块取 title/图
        marks = [(m.start(), m.group(2), m.group(1)) for m in RE_ATAG.finditer(html)]
        marks.append((len(html), None, None))
        for i in range(len(marks) - 1):
            vid = marks[i][1]
            if vid in seen:
                continue
            seen.add(vid)
            blk = html[marks[i][0]:marks[i + 1][0]]
            if len(blk) > 4000:
                blk = blk[:4000]
            m2 = re.search(r'title="([^"]*)"', marks[i][2])
            title = self._clean(m2.group(1)) if m2 else ""
            if not title:
                m2 = RE_TITLE2.search(blk)
                title = self._clean(m2.group(1)) if m2 else ""
            if strict and not title:
                continue
            title = title or ("影片 " + vid)
            pic = self._pic(title, self._pick_pic(blk, vid, html))
            # ★推荐块(class pLinks)里的条目没有图且 id 会在首页重复出现 → 列表里剔除，
            #   详情页仍可从播放页回查封面（detail 已有回退）
            if strict and not pic:
                continue
            if vid in _PICS:
                if not _PICS[vid].get("pic"):
                    _PICS[vid]["pic"] = pic
                _PICS[vid].setdefault("name", title)
            else:
                _PICS[vid] = {"name": title, "pic": pic}
            items.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": self.img_mode == "1" and pic.startswith("http") and self._proxy_base()
                            and self._proxy_url_for(pic, True) or pic,
                "vod_remarks": "",
            })
        return items

    def _pagecount(self, html):
        nums = [int(x) for x in RE_PAGELAST.findall(html or "")]
        return max(nums) if nums else 0

    # ---------------- 六接口 ----------------
    def homeContent(self, filter=True):
        cls = [{"type_id": t, "type_name": n} for t, n in self._nav()
               if t not in BLOCK_TID]
        result = {"class": cls}
        try:
            html = self._get("/cn/home/web/index.php/vod/type/id/20.html") or ""
            lst = self._parse_list(html, strict=False)[:40]
            if lst:
                result["list"] = lst
        except Exception:
            pass
        return result

    def homeVideoContent(self):
        try:
            html = self._get("/cn/home/web/index.php/vod/type/id/20.html") or ""
            return {"list": self._parse_list(html, strict=False)[:40]}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg, filter=True, extend=None):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        tid = str(tid).strip()
        if tid.startswith("http"):
            tid = re.search(r'/id/(\d+)', tid).group(1) if re.search(r'/id/(\d+)', tid) else "20"
        path = ("/cn/home/web/index.php/vod/type/id/%s.html" % tid) if page == 1 else \
               ("/cn/home/web/index.php/vod/type/id/%s/page/%d.html" % (tid, page))
        try:
            html = self._get(path)
            # 无效 tid 时站点会回落推荐内容（34 条固定不变）→ 判定为无数据，不伪造
            if html and ("/type/id/%s" % tid) not in html and tid not in ("20",):
                return {"list": [], "page": page, "pagecount": page, "limit": 40, "total": 0}
            lst = self._parse_list(html)
            if not lst and page > 1:
                # 空页刹车：不再往下编
                return {"list": [], "page": page, "pagecount": page, "limit": 40, "total": 0}
            pc = self._pagecount(html) or (page + 1 if lst else page)
            return {"list": lst, "page": page, "pagecount": max(pc, page),
                    "limit": len(lst) or 40, "total": 0}
        except Exception:
            return {"list": [], "page": page, "pagecount": page, "limit": 40, "total": 0}

    def detailContent(self, ids):
        vid = ""
        try:
            raw = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
            m = re.search(r'(\d+)', str(raw))
            vid = m.group(1) if m else str(raw).strip()
        except Exception:
            pass
        if not vid:
            return {"list": []}
        memo = _PICS.get(vid, {})
        name, pic = memo.get("name", ""), memo.get("pic", "")
        content = ""
        path = "/cn/home/web/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid
        try:
            html = self._get(path)
            if html:
                t = re.search(r'<title>(.*?)</title>', html, re.S)
                if t:
                    name = self._clean(re.split(r'\s*[-_|]\s*', t.group(1))[0]) or name
                d = re.sub(r'<[^>]+>', ' ', html)
                c = re.search(r'(分类[：:][^模]{0,30})', d)
                if c:
                    content = self._clean(c.group(1))
                if not pic:
                    m = RE_DATAORIG.search(html) or RE_SRC.search(html)
                    if m:
                        pic = self._abs(m.group(1))
        except Exception:
            pass
        name = name or ("影片 " + vid)
        if self.img_mode == "1" and pic.startswith("http") and "?do=py" not in pic:
            v = self._img_via(pic)
            if v:
                pic = v
        v = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": "正片",
            "vod_content": content or "单集单线路，明文 m3u8 直出。",
            "vod_play_from": "小马拉大车",
            "vod_play_url": "正片$" + vid,
        }
        return {"list": [v]}

    def playerContent(self, flag, id, vipFlags=None):
        vid = ""
        m = re.search(r'(\d+)', str(id))
        if m:
            vid = m.group(1)
        url = ""
        if vid:
            path = "/cn/home/web/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid
            try:
                html = self._get(path, timeout=20)
                if html:
                    pm = RE_PLAYER.search(html)
                    if pm:
                        raw = pm.group(1).replace("\\/", "/")
                        u = RE_JSONURL.search(raw)
                        if u:
                            url = u.group(1).replace("\\/", "/").replace("\\u0026", "&")
                    if not url:
                        for pat in (r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)',):
                            u2 = re.search(pat, html)
                            if u2:
                                url = u2.group(1)
                                break
            except Exception:
                url = ""
        hdr = {"User-Agent": random.choice(UA_POOL)}
        if url:
            return {"parse": 0, "url": url, "header": dict(hdr)}
        # 拿不到直链：交回播放页让宿主内建嗅探兜底
        return {"parse": 0, "url": self.host + path, "header": dict(hdr)}

    def searchContent(self, key, quick=False, pg="1"):
        kw = urllib.parse.quote(str(key or "").strip())
        try:
            html = self._get("/cn/home/web/index.php/vod/search.html?wd=" + kw)
            lst = self._parse_list(html)
            # 站点搜索无真分页，诚实 pagecount=1（不伪造 9999）
            return {"list": lst, "page": 1, "pagecount": 1, "limit": len(lst) or 20, "total": 0}
        except Exception:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}