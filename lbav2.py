# -*- coding: utf-8 -*-
"""
萝卜视频 lbav102.cc  TVBox / FongMi  type=3 Python 源
站型: MacCMS v10 + MDassets(madou) 模板
要点(逆向结论):
 1. 全站 HTML 被一层 document.write(decodeURIComponent(atob("..."))) 包住 -> 必须先解这层, 否则正则全空
 2. /api.php/provide/vod 返回 "域名未授权" -> 采集接口已关, 只能 HTML 直抓
 3. 没有详情页: 列表卡片 a[href=/index.php/vod/play/id/X/sid/1/nid/1.html] 直接就是播放页(单集)
 4. 播放页 var player_aaaa={... "url":"/api/m3u8/{from}/{hash}.m3u8","encrypt":0,...}
    - 免费片(限免/普通)才有 player_aaaa, VIP 片页面完全没有该变量(登录/付费墙) -> 不硬解, 详情里标注
 5. m3u8 站内相对路径, EXT-X-KEY URI="/api/app/vid/sec", 分片 /lxxx.php?id=xxx 302 到站外 CDN
    -> 播放必须走本地代理重写相对路径+带 Referer, 否则播放器拿不到
 6. URL:
    列表   /index.php/vod/show[/by/{time|hits_week|up}]/id/{tid}[/page/{pg}].html
    标签   /index.php/label/{new|free|rank}.html
    搜索   /index.php/vod/search/page/{pg}/wd/{quote}.html  (也支持 by/time)
    播放   /index.php/vod/play/id/{id}/sid/1/nid/1.html
"""
import base64
import json
import re
import urllib.parse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HOSTS = ["https://lbav102.cc", "https://lbav103.cc", "https://lb51.vip"]

CLS = [
    ("1", "全部"), ("2", "国产热播"), ("3", "校园"), ("6", "少妇"),
    ("7", "户外"), ("8", "剧情"), ("9", "模特"), ("10", "明星"),
    ("11", "解说"), ("12", "青春"), ("13", "自拍"),
]
# 站点原名 -> tid 兼容(用户手动传旧名也能命中)
CLS_ALIAS = {
    "萝莉学生": "3", "淫荡孕妇": "6", "车震户外": "7", "强奸迷奸": "8",
    "探花寻欢": "9", "明星淫梦": "10", "国语解说": "11", "18岁": "12", "自拍偷拍": "13",
}
ORDERS = [("time", "最新"), ("hits_week", "最热"), ("up", "最赞")]

_CACHE = {}
_HOST = {"i": 0}
CDN_BASE = "https://zzzhf.az1vu.cn"  # lxxx.php 302 后的最终分片域
_PIC = {}   # vid -> 封面(列表/搜索阶段登记, 壳每次 new Spider 故必须模块级)
_NAME = {} # vid -> 片名

RE_ATOB = re.compile(r'atob\("([^"]+)"\)')
RE_PLAY = re.compile(r'/index\.php/vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)\.html')
RE_CARD = re.compile(
    r'href="(/index\.php/vod/play/id/(\d+)/sid/\d+/nid/\d+\.html)"[^>]*>\s*'
    r'<div class="card">(.{0,2600}?)</a>', re.S)
RE_IMG = re.compile(r'data-src="([^"]+)"')
RE_SUB = re.compile(r'<div class="subtitle">(.*?)</div>', re.S)
RE_PLAYER = re.compile(r'player_aaaa\s*=\s*(\{.*?\})</script>', re.S)
RE_PAGES = re.compile(r'/page/(\d+)\.html')
RE_PGMAX = re.compile(r'page/(\d+)\.html[^>]*>([^<]{0,10})</a>')


def _deobf(html):
    """剥掉外层 document.write(decodeURIComponent(atob(...)))"""
    m = RE_ATOB.search(html)
    if not m:
        return html
    try:
        return urllib.parse.unquote(base64.b64decode(m.group(1)).decode("utf-8", "ignore"))
    except Exception:
        return html


RE_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def _page_title(html):
    m = RE_TITLE.search(html or "")
    if not m:
        return ""
    t = _clean(m.group(1))
    t = re.sub(r"^在线播放\s*", "", t)
    t = re.sub(r"\s*[-|]\s*(在线播放)?\s*线路.*$", "", t)
    t = re.sub(r"\s*[-|]\s*高清资源.*$", "", t)
    t = re.sub(r"\s*[-|]\s*萝卜视频.*$", "", t)
    return t.strip()


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", s).strip().replace("$", "").replace("#", "")


class Spider(BaseSpider):

    def getName(self):
        return "萝卜视频"

    def getDependence(self):
        return []

    def init(self, extend=""):
        cfg = {}
        try:
            if extend:
                cfg = extend if isinstance(extend, dict) else json.loads(extend)
                if isinstance(cfg, dict) and cfg.get("host"):
                    HOSTS.insert(0, str(cfg["host"]).rstrip("/"))
        except Exception:
            cfg = {}
        self.headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://lbav102.cc/"}
        self.ext = dict(cfg) if isinstance(cfg, dict) else {}

    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.headers = {"User-Agent": UA}
        self.ext = {}

    # ---------- 网络 ----------
    def _raw(self, url, referer=None, timeout=20):
        h = dict(self.headers)
        if referer:
            h["Referer"] = referer
        h.pop("Accept-Encoding", None)
        import urllib.request as U
        req = U.Request(url, headers=h)
        try:
            resp = U.urlopen(req, timeout=timeout)
            data = resp.read()
        except Exception:
            return ""
        if data[:2] == b"\x1f\x8b":
            import zlib
            try:
                data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
            except Exception:
                pass
        try:
            return data.decode("utf-8", "ignore")
        except Exception:
            return data.decode("gbk", "ignore")

    def _get(self, path, timeout=20):
        """按域池轮换抓, 缓存 180s"""
        if not path.startswith("http"):
            ck = path
            hit = _CACHE.get(ck)
            if hit and hit[0] > _now():
                return hit[1]
            out = ""
            for i in range(len(HOSTS)):
                host = HOSTS[(_HOST["i"] + i) % len(HOSTS)]
                out = _deobf(self._raw(host + path, timeout=timeout))
                if len(out) > 3000 and "vod/play" in out:
                    _HOST["i"] = (_HOST["i"] + i) % len(HOSTS)
                    break
            if out:
                _CACHE[ck] = (_now() + 180, out)
            return out
        return self._raw(path, timeout=timeout)

    # ---------- 解析 ----------
    def _cards(self, html):
        out = []
        for m in RE_CARD.finditer(html or ""):
            href, vid, body = m.group(1), m.group(2), m.group(3)
            t = RE_SUB.search(body)
            name = _clean(t.group(1)) if t else ""
            if not name:
                continue
            pic = ""
            im = RE_IMG.search(body)
            if im:
                pic = im.group(1)
            if pic.startswith("//"):
                pic = "https:" + pic
            if pic and ("loading" in pic or "placeholder" in pic):
                pic = ""
            _PIC[vid] = pic
            _NAME[vid] = name
            out.append({"vod_id": vid, "vod_name": name, "vod_pic": pic, "vod_remarks": ""})
        if not out:
            # 兜底: 只要 play 链接, 名字后面再补
            for m in RE_PLAY.finditer(html or ""):
                out.append({"vod_id": m.group(1), "vod_name": "", "vod_pic": "", "vod_remarks": ""})
        seen = set()
        res = []
        for v in out:
            if v["vod_id"] in seen:
                continue
            seen.add(v["vod_id"])
            res.append(v)
        return res

    def _pagecount(self, html, pg):
        best = 1
        for m in RE_PAGES.finditer(html or ""):
            try:
                best = max(best, int(m.group(1)))
            except Exception:
                pass
        return min(max(best, pg), 20000)

    # ---------- 六接口 ----------
    def homeContent(self, filter=True):
        vods = self._cards(self._get("/"))
        return {"class": [{"type_id": t, "type_name": n} for t, n in CLS],
                "filters": self._filters(),
                "list": vods, "page": 1, "pagecount": 1, "limit": len(vods), "total": len(vods)}

    def _filters(self):
        f = [{"key": "by", "name": "排序", "value": [{"n": n, "v": v} for v, n in ORDERS]}]
        for t, n in CLS:
            if t == "1":
                continue
            f.append({"key": "cid", "name": "分类", "value": [{"n": n, "v": t}]})
        return f

    def homeVideoContent(self):
        vods = self._cards(self._get("/index.php/label/new.html"))
        return {"list": vods, "page": 1, "pagecount": 1, "limit": len(vods), "total": len(vods)}

    def _tid(self, tid):
        tid = str(tid or "1").strip()
        if tid in CLS_ALIAS:
            return CLS_ALIAS[tid]
        for t, n in CLS:
            if t == tid or n == tid:
                return t
        return re.sub(r"\D", "", tid) or "1"

    def categoryContent(self, tid, pg, filter=False, extend=None):
        tid = self._tid(tid)
        pg = max(1, _aint(pg, 1))
        by = "time"
        cid = None
        if isinstance(extend, dict):
            by = extend.get("by") or "time"
            cid = extend.get("cid")
            if cid and cid != tid:
                tid = self._tid(cid)
        if pg == 1:
            url = "/index.php/vod/show/by/%s/id/%s.html" % (by, tid)
        else:
            url = "/index.php/vod/show/by/%s/id/%s/page/%d.html" % (by, tid, pg)
        html = self._get(url)
        vods = self._cards(html)
        pc = self._pagecount(html, pg)
        if not vods:
            pc = pg
        return {"list": vods, "page": pg, "pagecount": pc, "limit": len(vods), "total": pc * len(vods)}

    def searchContent(self, key, quick, pg="1"):
        pg = max(1, _aint(pg, 1))
        url = "/index.php/vod/search/page/%d/wd/%s.html" % (pg, urllib.parse.quote(str(key or "")))
        html = self._get(url)
        vods = self._cards(html)
        pc = self._pagecount(html, pg)
        if not vods:
            pc = pg
        return {"list": vods, "page": pg, "pagecount": pc, "limit": len(vods), "total": pc * len(vods)}

    def detailContent(self, ids):
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        out = []
        for vid in ids:
            vid = re.sub(r"\D", "", str(vid)) or "0"
            if not vid:
                continue
            html = self._get("/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid)
            m3u8 = _m3u8_path(_player_url(html))
            name, pic = _player_meta(html)
            if not name:
                name = _NAME.get(vid) or _page_title(html) or vid
            if not pic:
                pic = _PIC.get(vid) or ""
            v = {"vod_id": vid, "vod_name": name, "vod_pic": pic,
                 "vod_remarks": "免费" if m3u8 else "VIP"}
            if m3u8:
                lines = ["萝卜·直连$main@@" + vid,
                         "萝卜·代理播放$proxy@@" + vid,
                         "萝卜·CDN直连$cdn@@" + vid]
            else:
                # 免费片才给直链; VIP 片只有播放页, 交本地代理+壳内建嗅探兜底
                lines = ["萝卜·原页嗅探$sniff@@" + vid,
                         "萝卜·代理播放$proxy@@" + vid,
                         "萝卜·CDN直连$cdn@@" + vid]
            v["vod_play_from"] = "$$$".join(x.split("$")[0] for x in lines)
            v["vod_play_url"] = "$$$".join("正片$" + x.split("$", 1)[1] for x in lines)
            out.append(v)
        return {"list": out}

    def playerContent(self, flag, id, vipFlags=None):
        raw = str(id or "")
        mode = "main"
        vid = raw
        if "@@" in raw:
            mode, vid = raw.split("@@", 1)
        vid = re.sub(r"\D", "", vid) or "0"
        host = HOSTS[_HOST["i"]]
        ref = host + "/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid
        direct = _m3u8_path(_player_url(self._get("/index.php/vod/play/id/%s/sid/1/nid/1.html" % vid)))
        if mode == "cdn" and not direct:
            return {"parse": 0, "playUrl": "", "url": ref,
                    "header": {"User-Agent": UA, "Referer": host + "/"}}
        if mode == "sniff" and not direct:
            return {"parse": 0, "playUrl": "", "url": ref,
                    "header": {"User-Agent": UA, "Referer": host + "/"}}
        if direct and mode == "main":
            return {"parse": 0, "playUrl": "", "url": host + direct,
                    "header": {"User-Agent": UA, "Referer": ref}}
        if not direct:
            return {"parse": 0, "playUrl": "", "url": ref,
                    "header": {"User-Agent": UA, "Referer": host + "/"}}
        # 本地代理线: 相对路径/AES key/分片全部重写绝对化; kind=cdn 时把 302 目标域直接写死, 省一跳
        kind = "cdn" if mode == "cdn" else "proxy"
        try:
            from com.github.catvod import Proxy
            base = Proxy.getUrl(True)
        except Exception:
            base = "http://127.0.0.1:9978/proxy?do=py"
        u = base + "&siteKey=" + self.getName() + "&kind=" + kind + \
            "&url=" + urllib.parse.quote(host + direct, safe="")
        return {"parse": 0, "playUrl": "", "url": u,
                "header": {"User-Agent": UA, "Referer": ref}}

    # ---------- 本地代理: m3u8 绝对化 ----------
    def localProxy(self, param=None):
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except Exception:
                param = {"url": param}
        if not isinstance(param, dict):
            param = {}
        url = param.get("url") or ""
        kind = param.get("kind") or "proxy"
        if not url:
            return [404, "text/plain", b"", {}]
        try:
            from urllib.parse import urljoin, urlsplit, urlunsplit
            html = self._raw(url, referer=url)
            if b"#EXTM3U" in html.encode("utf-8", "ignore")[:400] or "#EXTM3U" in html:
                from urllib.parse import urljoin, urlsplit, urlunsplit
                sp = urlsplit(url)
                base = urlunsplit((sp.scheme, sp.netloc, sp.path.rsplit("/", 1)[0] + "/", "", ""))
                out = []
                for ln in html.split("\n"):
                    ls = ln.strip()
                    if not ls or ls.startswith("#"):
                        if 'URI="' in ls:
                            ls = re.sub(r'URI="([^"]+)"',
                                        lambda m: 'URI="' + urljoin(base, m.group(1)) + '"', ls)
                        out.append(ls)
                        continue
                    if kind == "cdn":
                        mm = re.match(r"/[A-Za-z0-9_]+\.php\?id=(.+)$", ls)
                        if mm and ls.endswith(".ts"):
                            out.append(CDN_BASE + "/" + mm.group(1))
                            continue
                    out.append(urljoin(base, ls))
                body = "\n".join(out).encode("utf-8", "ignore")
                return [200, "application/vnd.apple.mpegurl", body,
                        {"User-Agent": UA, "Referer": url}]
            return [200, "application/octet-stream",
                    html.encode("utf-8", "ignore"),
                    {"User-Agent": UA, "Referer": url}]
        except Exception as e:
            return [500, "text/plain", str(e).encode("utf-8", "ignore"), {}]

    def isVideoFormat(self, url):
        return any(x in (url or "").lower() for x in
                   [".m3u8", ".mp4", ".flv", ".ts", ".mkv", ".avi", ".webm"])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass


def _now():
    import time
    return time.time()


def _aint(v, d=0):
    try:
        return int(str(v).strip())
    except Exception:
        return d


def _player_json(html):
    m = RE_PLAYER.search(html or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1).replace("\\/", "/"))
    except Exception:
        return {}


def _player_url(html):
    d = _player_json(html)
    u = d.get("url") or ""
    if not u:
        return ""
    return u.replace("\\/", "/")


def _player_meta(html):
    d = _player_json(html)
    vd = d.get("vod_data") or {}
    name = _clean(vd.get("vod_name") or "") or ""
    pic = (d.get("poster") or "").replace("\\/", "/")
    if pic.startswith("//"):
        pic = "https:" + pic
    return name, pic


def _m3u8_path(u):
    if not u:
        return ""
    u = u.replace("\\/", "/")
    if u.startswith("http"):
        m = re.search(r"(/api/m3u8/[^?\"']+)", u)
        return m.group(1) if m else u
    return u


Spider = Spider
