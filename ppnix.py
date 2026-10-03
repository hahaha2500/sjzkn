# -*- coding: utf-8 -*-
"""PPnix 影视源 - 默影视/TVBox/FongMi type=3 (Chaquopy Python)

站点: https://www.ppnix.com/cn/  (Emby/苹果CMS 系自写模板, 无采集接口)
结构:
  首页 /cn/            卡片 <a href="/cn/(tv|movie)/ID.html">
  详情 /cn/tv|ID.html   正文纯文本含 导演/主演/类型/国家/又名/简介/评分
                        页尾内联: classid=X;classurl='/tv/';infoid=ID;sub='|en|cn|tw|';m3u8=[集号...]
  取流 /info/m3u8/{infoid}/{集号}.m3u8   (AES-128, key=/info/m3u8/key)
  分类 /cn/movie.html 不存在 -> 用首页+详情反查, 分类以 电影/电视剧 两大类 + 语言
"""
import re, time, json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        def __init__(self):
            pass

HOST = "https://www.ppnix.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 模块级: 壳子可能每次 new Spider, 实例级状态会丢
_CACHE = {}
_PICS = {}
_GOOD_HOST = [HOST]


def _clean(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    return re.sub(r"[#$]", "", t)


def _cards(html):
    out = {}
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        m = re.search(r"/cn/(tv|movie)/(\d+)\.html", a["href"])
        if not m:
            continue
        kind, vid = m.group(1), m.group(2)
        url = urljoin(HOST, a["href"])
        pic = ""
        im = a.find("img")
        if im is not None:
            pic = im.get("data-src") or im.get("data-original") or im.get("src") or ""
            pic = urljoin(HOST, pic) if pic.startswith("/") else pic
        title = (a.get("title") or "").strip()
        if not title and im is not None:
            title = (im.get("alt") or "").strip()
        if not title:
            title = a.get_text(strip=True)
        if not title:
            nx = a.find_next("a", href=url)
            if nx is not None:
                title = (nx.get("title") or nx.get_text(strip=True) or "").strip()
        key = (kind, vid)
        rec = out.setdefault(key, {"kind": kind, "vid": vid, "url": url,
                                  "title": title, "pic": pic})
        if pic and not rec["pic"]:
            rec["pic"] = pic
        if title and not rec["title"]:
            rec["title"] = title
    return list(out.values())


class Spider(_BaseSpider):

    def __init__(self):
        super().__init__()
        try:
            self.extend = {}
        except Exception:
            self.extend = {}
        self.host = HOST
        self.ses = requests.Session()
        self.ses.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        self.timeout = 15

    # ---------- extend: 传 host 换镜像, probe 关掉分类探测 ----------
    def init(self, extend=""):
        conf = {}
        if isinstance(extend, dict):
            conf = extend
        elif isinstance(extend, str) and extend.strip():
            try:
                conf = json.loads(extend)
            except Exception:
                conf = {}
        h = conf.get("host")
        if h:
            self.host = h if h.startswith("http") else "https://" + h.strip("/")
            if _GOOD_HOST[0] != self.host:
                _GOOD_HOST.insert(0, self.host)
        return self

    # ---------- 网络单一入口 ----------
    def _get(self, url, ref=None, tries=3):
        for _ in range(tries):
            try:
                h = {"Referer": ref or (self.host + "/cn/")}
                r = self.ses.get(url, timeout=self.timeout, headers=h)
                if r.status_code == 200 and len(r.content) > 200:
                    if not r.encoding or r.encoding.lower() == "iso-8859-1":
                        r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
            except requests.RequestException:
                pass
            time.sleep(1.0)
        return ""

    def _get_bytes(self, url, ref=None, tries=2):
        for _ in range(tries):
            try:
                r = self.ses.get(url, timeout=20,
                                 headers={"Referer": ref or (self.host + "/cn/")},
                                 stream=True)
                if r.status_code == 200:
                    return r.raw.read(decode_content=True)
            except requests.RequestException:
                pass
            time.sleep(0.8)
        return b""

    def _cached(self, key, ttl, fn):
        it = _CACHE.get(key)
        if it and time.time() - it[0] < ttl:
            return it[1]
        v = fn()
        if v:
            _CACHE[key] = (time.time(), v)
            if len(_CACHE) > 300:
                for k in list(_CACHE)[:150]:
                    _CACHE.pop(k, None)
        return v

    # ---------- 首页 ----------
    def home_html(self):
        return self._cached("home", 300, lambda: self._get(self.host + "/cn/"))

    def homeContent(self, filter=False):
        html = self.home_html()
        vods = []
        for c in _cards(html):
            vods.append({"vod_id": c["kind"] + "@@" + c["vid"],
                         "vod_name": c["title"],
                         "vod_pic": c["pic"],
                         "vod_remarks": c["kind"]})
        if filter:
            f = [{"key": "by", "name": "线路"}]
        else:
            f = []
        return {"class": [{"type_id": "tv", "type_name": "电视剧"},
                          {"type_id": "movie", "type_name": "电影"},
                          {"type_id": "en", "type_name": "English"}],
                "filters": {"tv": f, "movie": f, "en": f},
                "list": vods[:60]}

    def homeVideoContent(self):
        return {"list": self.homeContent().get("list", [])}

    # ---------- 分类: 该站无列表页, 用首页条目按类型切 ----------
    def categoryContent(self, tid, pg, filter=None, extend=None):
        html = self.home_html()
        if not html:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        key = tid if tid in ("tv", "movie", "en") else "tv"
        all_c = _cards(html)
        if key == "en":
            out = [c for c in all_c
                   if "english" in (self._get(c["url"]) or "").lower()[:400]]
            if not out:
                out = all_c[:24]
        else:
            out = [c for c in all_c if c["kind"] == key]
        vods = [{"vod_id": c["kind"] + "@@" + c["vid"],
                 "vod_name": c["title"], "vod_pic": c["pic"],
                 "vod_remarks": key} for c in out]
        return {"list": vods, "page": 1, "pagecount": 1, "limit": len(vods),
                "total": len(vods)}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        try:
            ids = ids or []
            if isinstance(ids, str):
                ids = [ids]
        except Exception:
            return {"list": []}
        if not ids:
            return {"list": []}
        vid_id = ids[0]
        kind, _, vid = vid_id.partition("@@")
        if not vid:
            kind, vid = ("tv" if "/tv/" in vid_id else "movie"), vid_id
        url = "%s/cn/%s/%s.html" % (self.host, kind, vid)
        html = self._get(url)
        if not html:
            return {"list": []}

        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"&#\w+;", " ", text)

        title = ""
        mt = re.search(r"m3u8\s*=\s*(\[[^\]]*\])", html)
        eps = re.findall(r"['\"]([^'\"]+)['\"]", mt.group(1)) if mt else []
        if not eps:
            eps = ["1080P"] if kind == "movie" else ["1"]

        name = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        if name:
            title = _clean(re.sub(r"<[^>]+>", "", name.group(1)))
        if not title:
            og = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            title = _clean(og.group(1)) if og else vid

        year = ""
        my = re.search(r"\)\s+(19|20)\d{2}", text)
        my2 = re.search(r"\((19|20)\d{2}\)", text)
        if my2:
            year = my2.group(0).strip("()")
        score = ""
        ms = re.search(r"\)\s+([0-9]\.[0-9])\s+导演", text)
        if ms:
            score = ms.group(1)

        def grab(pat):
            m = re.search(pat, text)
            return m.group(1).strip(" /") if m else ""

        director = grab(r"导演[:：]?\s*(.+?)\s*主演[:：]")
        actors = grab(r"主演[:：]?\s*(.+?)\s*类型[:：]")
        genre = grab(r"类型[:：]?\s*(.+?)\s*国家[:：]")
        area = grab(r"国家[:：]?\s*(.+?)\s*(?:又名|简介)")
        alias = grab(r"又名[:：]?\s*(.+?)\s*简介[:：]")
        mdesc = re.search(r"简介[:：]?\s*(.+?)\s*分享到", text)
        desc = _clean(mdesc.group(1)) if mdesc else ""

        pic = ""
        opi = re.search(r'property="og:image"\s+content="([^"]+)"', html)
        if opi:
            pic = opi.group(1)
        if not pic:
            pic = _PICS.get(vid_id, "")
        if pic:
            _PICS[vid_id] = pic

        # 线路: 主线(直连) + 嗅探备用
        u = "%s/info/m3u8/%s/%%s.m3u8" % (self.host, vid)
        eps_main = "#".join("第%s集$%s" % (e, u % e) for e in eps)
        eps_sniff = "#".join("第%s集$%s/cn/%s/%s.html" % (e, self.host, kind, vid)
                             for e in eps)
        vod = {"vod_id": vid_id, "vod_name": title, "vod_pic": pic,
               "vod_year": year, "vod_remarks": score or ("共%d集" % len(eps)),
               "vod_actor": actors, "vod_director": director,
               "vod_area": area, "vod_class": genre, "vod_content": desc,
               "vod_play_from": "PPnix·主线$$$PPnix·嗅探",
               "vod_play_url": eps_main + "$$$" + eps_sniff}
        return {"list": [vod]}

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags=None):
        try:
            parts = (id or "").split("@@")
        except Exception:
            parts = [id]
        host = self.host
        ua = UA
        if parts and parts[0] == "sniff":
            return {"parse": 0, "url": parts[-1], "header": {"User-Agent": ua}}
        url = parts[-1] if parts else ""
        if url.startswith("/"):
            url = host + url
        # 首集直连; 失败交壳子内建嗅探
        return {"parse": 0, "url": url,
                "header": {"User-Agent": ua, "Referer": host + "/cn/"}}

    # ---------- 本地代理: 绝对化相对 key / 兜底 m3u8 ----------
    def localProxy(self, param=None):
        try:
            if isinstance(param, str):
                try:
                    d = json.loads(param)
                except Exception:
                    d = {"url": param}
            elif isinstance(param, dict):
                d = param
            else:
                d = {}
            url = d.get("url") or ""
            if not url:
                return [404, "text/plain", b"", {}]
            if url.startswith("/"):
                url = self.host + url
            elif url.startswith("../"):
                url = self.host + "/info/m3u8/" + url[3:]
            if "/info/m3u8/key" in url:
                b = self._get_bytes(url)
                return [200, "application/octet-stream", b, {}]
            b = self._get_bytes(url)
            return [200, "application/vnd.apple.mpegurl", b, {}]
        except Exception:
            return [404, "text/plain", b"", {}]

    # ---------- 搜索 ----------
    def searchContent(self, key, quick="", pg="1"):
        res = []
        k = (key or "").strip()
        if not k:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        for p in ["/cn/", "/cn/movie.html", "/cn/tv.html", "/cn/en.html"]:
            html = self._get(self.host + p)
            for c in _cards(html):
                if k in c["title"]:
                    res.append({"vod_id": c["kind"] + "@@" + c["vid"],
                                "vod_name": c["title"], "vod_pic": c["pic"],
                                "vod_remarks": p})
        seen, out = set(), []
        for v in res:
            if v["vod_id"] in seen:
                continue
            seen.add(v["vod_id"])
            out.append(v)
        return {"list": out, "page": 1, "pagecount": 1, "limit": len(out),
                "total": len(out)}

    # ---------- 壳子必需 ----------
    def getDependence(self):
        return []

    def getName(self):
        return "PPnix影视"

    def isVideoFormat(self, url):
        return any(e in (url or "").lower()
                   for e in (".m3u8", ".mp4", ".flv", ".ts", ".mkv"))

    def manualVideoCheck(self):
        return False

    def playerContentHeader(self, flag=None):
        return {"User-Agent": UA, "Referer": self.host + "/cn/"}