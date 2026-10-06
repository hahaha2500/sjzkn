# -*- coding: utf-8 -*-
# 18jspa2.py  TVBox/FongMi type=3  默影视/webhtv 兼容
# 站型: MacCMS v10 自写 18j 模板 (18j61.cc) HTTPS-only + Cloudflare
# 播放: 详情页 JS  const source = "https://m3u8.cdn202608.com/.../index.m3u8" 明文
#       清单分片指向 25g.18j2026.com(部分网络不通) -> 加速线路改写到同路径 m3u8.cdn202608.com(实测200)
import json
import gzip
import zlib
import ssl
import re
import time
import html as _html
import threading
import urllib.request
import urllib.parse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

HOSTS = ["https://t3.18j61.cc", "https://t2.18j61.cc",
         "https://t1.18j61.cc", "https://18j61.cc"]

_CACHE = {}
_SLOCK = threading.Lock()
_GOOD = {"host": HOSTS[0]}
_ALIAS = {"乱伦系列": "14", "吃瓜黑料": "12", "探花现场": "13", "探花": "13",
          "SM重口": "35", "JAV自拍": "20", "jav无码": "22", "AV解说": "23"}


def _cget(key, ttl):
    with _SLOCK:
        it = _CACHE.get(key)
    if not it or time.time() - it[0] > ttl:
        return None
    return it[1]


def _cput(key, val):
    with _SLOCK:
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[key] = (time.time(), val)


def _clean(t):
    return _html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def _vid(url):
    m = re.search(r"18j61\.cc(/[^\s\"'<>]*)", url or "")
    if m:
        return m.group(1)
    return url if (url or "").startswith("/") else url


class Spider(BaseSpider):
    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = _GOOD["host"]
        self.proxy = None
        self.headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

    # ---------------- 基础 ----------------
    def getName(self):
        return "18J聚合"

    def init(self, extend=""):
        try:
            ex = json.loads(extend) if extend and extend.strip().startswith("{") else {}
        except Exception:
            ex = {}
        if isinstance(ex, dict):
            if ex.get("host"):
                self.host = str(ex["host"]).rstrip("/")
            if ex.get("proxy"):
                self.proxy = str(ex["proxy"])

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        low = (url or "").lower()
        return any(e in low for e in (".m3u8", ".mp4", ".flv", ".ts", ".mkv", ".avi"))

    def destroy(self):
        pass

    def _ctx(self):
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c

    def _raw(self, url, timeout=12, referer=None):
        h = dict(self.headers)
        if referer:
            h["Referer"] = referer
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ctx()),
            urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy} if self.proxy else {}))
        try:
            with opener.open(urllib.request.Request(url, headers=h), timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc or raw[:2] == b"\x1f\x8b":
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                elif "deflate" in enc:
                    try:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    except Exception:
                        pass
                for cs in ("utf-8", "gbk", "gb2312"):
                    try:
                        return raw.decode(cs)
                    except Exception:
                        continue
                return raw.decode("utf-8", "ignore")
        except Exception:
            return ""

    def _get(self, url, ttl=0, referer=None):
        ck = (url, referer or "")
        if ttl:
            c = _cget(ck, ttl)
            if c is not None:
                return c
        for h in [self.host] + [x for x in HOSTS if x != self.host]:
            real = url if url.startswith("http") else h + url
            txt = self._raw(real, referer=referer or (h + "/"))
            if len(txt) > 800 and ("</html>" in txt or "<li" in txt or "player" in txt):
                self.host = h
                _GOOD["host"] = h
                if ttl:
                    _cput(ck, txt)
                return txt
        if ttl:
            _cput(ck, "")
        return ""

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        classes = [{"type_id": "1", "type_name": "国产"}, {"type_id": "2", "type_name": "日韩"},
                    {"type_id": "3", "type_name": "欧美"}, {"type_id": "4", "type_name": "伦理"},
                    {"type_id": "5", "type_name": "动漫"}, {"type_id": "6", "type_name": "另类"},
                    {"type_id": "9", "type_name": "成人AI"}]
        sorts = [{"n": "最近更新", "v": "time"}, {"n": "今日热播", "v": "hits_day"},
                 {"n": "今日点赞", "v": "up_day"}, {"n": "好评榜", "v": "up"}]
        subs = {
            "1": [("全部", "1"), ("国产自拍", "11"), ("热门资讯", "12"), ("现场实录", "13"),
                  ("家庭剧情", "14"), ("国产av", "15"), ("福利姬", "16"), ("自慰诱惑", "17"), ("成人主播", "18")],
            "2": [("全部", "2"), ("无码中字", "19"), ("自拍演绎", "20"), ("中文字幕", "21"),
                  ("日韩合集", "22"), ("作品解说", "23")],
            "3": [("全部", "3"), ("欧美大片", "24"), ("黑人专区", "25"), ("留学生", "26")],
            "4": [("全部", "4"), ("港台三级", "27"), ("日韩三级", "28"), ("欧美三级", "29")],
            "5": [("全部", "5"), ("3D动漫", "30"), ("二次元", "31")],
            "6": [("全部", "6"), ("男同", "32"), ("女同", "33"), ("伪娘", "34"),
                  ("重口特写", "35"), ("东南亚", "45")],
            "9": [("全部", "9"), ("AI短剧", "36"), ("明星脸", "37"), ("成人漫剧", "46")],
        }
        filters = {}
        for tid, arr in subs.items():
            filters[tid] = [{"key": "sub_id", "name": "子分类",
                             "value": [{"n": n, "v": v} for n, v in arr]},
                            {"key": "by", "name": "排序", "value": sorts}]
        return {"class": classes, "filters": filters}

    def homeVideoContent(self):
        return self._category("1", 1, {}, "time")

    # ---------------- 列表 ----------------
    def _cat_url(self, tid, pg, by):
        if pg <= 1:
            return "/show/%s/by/%s/" % (tid, by) if by and by != "time" else "/t/%s/" % tid
        return "/show/%s/by/%s/page/%s/" % (tid, by, pg) if by and by != "time" \
            else "/show/%s/page/%s/" % (tid, pg)

    def _cards(self, doc):
        cards = []
        seen = set()
        if not doc:
            return cards
        blocks = re.findall(r"<li\b[^>]*>(.*?)</li>", doc, re.I | re.S)
        if len(blocks) < 3:
            blocks = re.findall(r'<div class="box">(.*?)</div>\s*</div>', doc, re.I | re.S)
        if len(blocks) < 3:
            blocks = [doc]
        for blk in blocks:
            m = re.search(r'href="((?:https?://[^"]*?18j61\.cc)?/v/[^"]+)"', blk, re.I)
            if not m:
                continue
            vid = _vid(m.group(1))
            if vid in seen:
                continue
            name = ""
            for pat in (r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h3>',
                        r'<img[^>]*alt="([^"]*)"', r'title="([^"]*)"'):
                t = re.search(pat, blk, re.I | re.S)
                if t:
                    name = _clean(t.group(1))
                    if len(name) >= 2:
                        break
            if not name:
                continue
            pic = ""
            pm = re.search(r'(?:data-original|data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', blk, re.I)
            if pm:
                pic = pm.group(1).strip()
                if pic.startswith("//"):
                    pic = "https:" + pic
                elif not pic.startswith("http"):
                    pic = self.host + pic
            rem = ""
            rm = re.search(r'<div class="vodlist_img">.*?<span>([^<]*)</span>', blk, re.I | re.S)
            if not rm:
                rm = re.search(r'<div class="sub">.*?<a[^>]*>([^<]*)</a>', blk, re.I | re.S)
            if rm:
                rem = _clean(rm.group(1))
            seen.add(vid)
            cards.append({"vod_id": vid, "vod_name": name, "vod_pic": pic,
                          "vod_remarks": rem or "高清",
                          "style": {"type": 4, "span": 3, "ratio": 0.56}})
        return cards

    def _category(self, tid, pg, extend, sort_def="time"):
        ext = extend or {}
        tid = str(_ALIAS.get(str(tid), tid) or tid)
        if ext.get("sub_id"):
            tid = str(_ALIAS.get(str(ext["sub_id"]), ext["sub_id"]))
        by = str(ext.get("by") or sort_def or "time")
        pg = int(pg) if str(pg).isdigit() else 1
        cards = self._cards(self._get(self._cat_url(tid, pg, by), ttl=120 if pg == 1 else 0))
        pc = pg + 1 if len(cards) >= 12 else pg
        return {"list": cards, "page": pg, "pagecount": pc,
                "limit": len(cards), "total": len(cards) * pc}

    def categoryContent(self, tid, pg, filter, extend):
        return self._category(tid, pg, extend)

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if str(pg).isdigit() else 1
        k = urllib.parse.quote(key)
        url = "/s/wd/%s/" % k if pg <= 1 else "/s/wd/%s/page/%s/" % (k, pg)
        cards = self._cards(self._get(url, ttl=60 if pg == 1 else 0))
        pc = pg + 1 if len(cards) >= 12 else pg
        return {"list": cards, "page": pg, "pagecount": pc,
                "limit": len(cards), "total": len(cards) * pc}

    # ---------------- 详情 ----------------
    def _media(self, doc):
        for p in (r'const\s+source\s*=\s*"(https?://[^"]+)"',
                  r'"contentUrl"\s*:\s*"(https?://[^"]+)"',
                  r'"url"\s*:\s*"(https?://[^"]+?\.m3u8[^"]*)"',
                  r'(https?://[^"\'\s]+?\.m3u8)'):
            m = re.search(p, doc or "", re.I)
            if m:
                u = m.group(1).replace("\\/", "/")
                if "sample" not in u:
                    return u
        return ""

    def detailContent(self, ids):
        vid = _vid(str(ids[0] if isinstance(ids, (list, tuple)) else ids))
        doc = self._get(vid, ttl=0)
        if not doc:
            return {"list": [{"vod_id": vid, "vod_name": vid,
                              "vod_play_from": "主线直连$$$加速线路$$$原页嗅探",
                              "vod_play_url": "正片$" + self.host + vid + "$$$正片$accel@@" + vid +
                                              "$$$正片$sniff@@" + (self.host + vid)}]}
        name = ""
        m = re.search(r'<h1[^>]*class="play-title"[^>]*>(.*?)</h1>', doc, re.I | re.S) \
            or re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.I | re.S)
        if m:
            name = _clean(m.group(1))
        pm = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', doc, re.I)
        pic = pm.group(1).strip() if pm else ""
        gm = re.search(r'"genre"\s*:\s*"([^"]*)"', doc)
        genre = gm.group(1) if gm else ""
        dm = re.search(r'<div class="footer-seo-rich-content vod-desc">(.*?)</div>', doc, re.I | re.S)
        desc = _clean(dm.group(1))[:400] if dm else ""
        dur = ""
        du = re.search(r'"duration"\s*:\s*"PT(\d+)M(\d+)S"', doc)
        if du:
            dur = "时长%s分%s秒" % (du.group(1), du.group(2))
        dt = re.search(r'更新日期：\s*([0-9\-]+)', doc)
        date = dt.group(1) if dt else ""

        media = self._media(doc)
        du_ = self.host + vid
        if media:
            urls = ["正片$" + media, "正片$accel@@" + vid, "正片$sniff@@" + du_]
        else:
            urls = ["正片$" + du_, "正片$accel@@" + vid, "正片$sniff@@" + du_]
        vod = {"vod_id": vid, "vod_name": name or "18J", "vod_pic": pic,
               "type_name": genre or "18J", "vod_year": date, "vod_area": "",
               "vod_remarks": dur or "高清", "vod_actor": "", "vod_director": "",
               "vod_content": desc or name,
               "vod_play_from": "主线直连$$$加速线路$$$原页嗅探",
               "vod_play_url": "$$$".join(urls)}
        return {"list": [vod]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        pid = str(id or "")
        hdr = {"User-Agent": UA, "Referer": self.host + "/"}
        if pid.startswith("sniff@@"):
            return {"parse": 0, "url": pid[7:], "header": hdr}
        if pid.startswith("accel@@"):
            vid = pid[7:]
            m = self._media(self._get(vid, ttl=0))
            if not m:
                return {"parse": 0, "url": self.host + vid, "header": hdr}
            return {"parse": 0, "url": self._accel(m), "header": hdr}
        return {"parse": 0, "url": pid, "header": hdr}

    # ---------------- 本地代理 ----------------
    def _pbase(self):
        try:
            from com.github.catvod import Proxy
            u = Proxy.getUrl(True)
            if u:
                return str(u).rstrip("/")
        except Exception:
            pass
        return "http://127.0.0.1:9978/proxy"

    def _accel(self, m3u8):
        b = self._pbase()
        if not b:
            return m3u8
        return b + "?do=py&url=" + urllib.parse.quote(m3u8, safe="")

    def localProxy(self, param=None):
        try:
            if isinstance(param, str):
                try:
                    p = json.loads(param)
                except Exception:
                    return [200, "text/plain", b"bad param", {}]
            elif isinstance(param, dict):
                p = param
            else:
                return [200, "text/plain", b"bad param", {}]
            url = urllib.parse.unquote(str(p.get("url") or ""))
            if not url.startswith("http"):
                return [404, "text/plain", b"bad url", {}]
            txt = self._raw(url, timeout=15, referer=self.host + "/")
            if not txt or "#EXTM3U" not in txt:
                return [404, "text/plain", b"empty", {}]
            body = txt.encode("utf-8", "ignore")
            body = re.sub(rb"(https?://)[a-z0-9.\-]*18j2026\.com/", rb"\1m3u8.cdn202608.com/", body)
            return [200, "application/vnd.apple.mpegurl", body,
                    {"User-Agent": UA, "Referer": self.host + "/"}]
        except Exception:
            return [200, "text/plain", b"error", {}]

    def action(self, action):
        return {}
