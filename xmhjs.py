# -*- coding: utf-8 -*-
"""泄密合集社 TVBox 源（type=3 Chaquopy Python）

站点逆向要点（2026-10-02 实测，换站必读）
--------------------------------------------------------------------------------
1) 站型 = 苹果CMS v10，但**路径前缀特殊**：所有页面在 /cn/home/web/index.php/vod/ 下，
   常规的 /index.php/vod/ 会 404。
2) **用户给的 /xmhjs/ 是站群别名路径**：站内链接一律写成 /cn/home/web/...（不带 /xmhjs），
   真实站点根 = https://fnq.xmhjs8.best  ；走 /xmhjs/cn/home/web/... 全部 404。
3) **采集接口已关**（/api.php/provide/vod 404），只能 HTML 直抓。
4) **没有详情页**：模板直接给播放页。首页/列表页卡片 href 全是
   /cn/home/web/index.php/vod/play/id/{id}/sid/{sid}/nid/{nid}.html
   （detail 页只有 349B 空壳，别去碰）
5) **列表页 = 分类页**：/vod/type/id/{tid}/page/{pg}.html（第1页也可用 /page/1.html）
   tid 实测 20~30 共 11 个分类；tid=20 有 586 页。
6) **单集单线路**：实测只有 sid=1/nid=1 有流，sid=2/nid=2 的 player_data.url 为空。
7) 播放页：<script>var player_data={"flag":"play","encrypt":0,...,"url":"明文m3u8"}</script>
   **encrypt=0 且 url 是明文 m3u8** → parse:0 直连，实测无 Referer 也 200（不需防盗链头）。
8) 播放页 <title> 被站方公告覆盖（"【https://…】永久网址"），**片名不能从播放页取**，
   故 vod_id 里直接编码 片名+封面（base64），详情页零额外请求。
9) 站群：首页挂着 14 个兄弟站（域名+目录双变），内容各不相同，仅作参考不作容灾。

URL 规律速查
  分类/列表  {base}/cn/home/web/index.php/vod/type/id/{tid}/page/{pg}.html
  播放        {base}/cn/home/web/index.php/vod/play/id/{vid}/sid/{sid}/nid/{nid}.html
  搜索        {base}/cn/home/web/index.php/vod/search/page/{pg}.html?wd={quote}
"""
import json
import re
import base64
import time
import random
import hashlib
import threading
import traceback

try:  # FongMi/Chaquopy 壳
    from base.spider import Spider as _Base
except Exception:  # 纯客户端壳：裸类也能加载
    class _Base(object):
        pass

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; U; Android 12; zh-cn) AppleWebKit/537.36 Version/4.0 Chrome/107.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Version/16.6 Mobile/15E148 Safari/604.1",
]
HOSTS = ["https://fnq.xmhjs8.best"]
WEB = "/cn/home/web/index.php/vod"
SITE = "泄密合集社"
CLS = [
    ("20", "最新主播网红"), ("21", "最新偷拍自拍"), ("22", "最新人妻熟女"),
    ("23", "最新强奸乱伦"), ("24", "最新制服丝袜"), ("25", "最新自慰变态"),
    ("26", "最新国产精品"), ("27", "最新亚洲情色"), ("28", "最新卡通动漫"),
    ("29", "最新三级伦理"), ("30", "最新欧美精品"),
]
BADIMG = ("placeholder", "loading", "default", "1x1", "blank", "spacer")

_SESSION = None
_LOCK = threading.Lock()
_CACHE = {}          # 模块级：壳子可能每次 new Spider，实例字段会丢
_CSTAT = {"n": 0, "hit": 0, "ok": 0}


# --------------------------------------------------------------------------- 网络
def _sess():
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            s = None
            try:
                import requests
                s = requests.Session()
                a = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=1)
                s.mount("http://", a)
                s.mount("https://", a)
            except Exception:
                pass
            if s is not None:
                _SESSION = s
                return s
            import urllib.request
            _SESSION = urllib.request
            return s
        return _SESSION


def _headers(self=None):
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://fnq.xmhjs8.best/",
    }


def _get(self, path, referer=None, nocache=False):
    """统一入口：域池容灾 + TTL 缓存 + 短超时重试
    nocache=True 用于播放页等时效资源：有时效的产物绝不缓存（时效资源铁律）"""
    ck = path[:400]
    hit = None if nocache else _CACHE.get(ck)
    if hit and time.time() - hit[0] < 180:
        with _LOCK:
            _CSTAT["hit"] += 1
        return hit[1]
    urls = ([self.host + path] if getattr(self, "host", "") else
            [h + path for h in HOSTS])
    if referer:
        urls = urls
    txt = ""
    for u in urls:
        for _ in range(2):
            try:
                s = _sess()
                hd = _headers()
                if referer:
                    hd["Referer"] = referer
                if hasattr(s, "get"):
                    r = s.get(u, headers=hd, timeout=(6, 14))
                    if r.status_code != 200:
                        continue
                    txt = r.text
                else:
                    import urllib.request
                    req = urllib.request.Request(u, headers=hd)
                    with urllib.request.urlopen(req, timeout=15) as fp:
                        txt = fp.read().decode("utf-8", "replace")
                if txt and "页面迷路了" not in txt[:4000]:
                    with _LOCK:
                        _CSTAT["ok"] += 1
                    if not nocache:
                        _CACHE[ck] = (time.time(), txt)
                    return txt
                txt = txt or ""
            except Exception:
                time.sleep(0.4)
    return txt


# --------------------------------------------------------------------------- 解析
def _b64e(s):
    try:
        return base64.b64encode((s or "").encode("utf-8")).decode("ascii").replace("=", "")
    except Exception:
        return ""


def _b64d(s):
    try:
        s = (s or "").replace("-", "+").replace("_", "/")
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
    except Exception:
        return ""


def _clean(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    t = t.replace("$", "").replace("#", "").strip()
    return re.sub(r"\s{2,}", " ", t)


def _mkid(vid, sid, nid, title, pic):
    return "p@@%s@@%s@@%s@@%s@@%s" % (vid, sid, nid, _b64e(title), _b64e(pic))


def _parseid(s):
    p = str(s or "").split("@@")
    if len(p) >= 6 and p[0] == "p":
        return p[1], p[2], p[3], _b64d(p[4]), _b64d(p[5])
    return p[1] if len(p) > 1 else "1", "1", "1", "", ""


RE_CARD = re.compile(
    r'<a[^>]+href="([^"]*vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)\.html)"[^>]*>(.{0,900}?)</a>',
    re.S)


def _cards(self, html):
    out, seen = [], set()
    for m in RE_CARD.finditer(html or ""):
        href, vid, sid, nid, inner = m.groups()
        if vid in seen:
            continue
        seen.add(vid)
        im = re.search(r'<img[^>]+src="([^"]+)"', inner)
        pic = im.group(1).strip() if im else ""
        if not pic or any(b in pic.lower() for b in BADIMG):
            for a in ("data-original", "data-src", "data-lazy-src", "data-poster"):
                im2 = re.search(a + r'="([^"]+)"', inner)
                if im2 and im2.group(1).strip():
                    pic = im2.group(1).strip()
                    break
        tm = re.search(r'class="[^"]*video-title[^"]*"[^>]*>(.*?)</span>', inner, re.S)
        title = _clean(tm.group(1)) if tm else ""
        if not title:
            title = re.search(r'title="([^"]+)"', inner)
            title = _clean(title.group(1)) if title else vid
        rm = re.search(r'class="label-private[^"]*"[^>]*>\s*([^<]{1,10})', inner)
        note = _clean(rm.group(1)) if rm else ""
        if not note:
            dm = re.search(r'class="duration[^"]*"[^>]*>\s*([^<]{1,20})', inner)
            note = _clean(dm.group(1)) if dm else ""
        if not pic:
            pic = ""
        out.append({
            "vod_id": _mkid(vid, sid, nid, title, pic),
            "vod_name": title[:120],
            "vod_pic": pic,
            "vod_remarks": note[:20],
            "style": {"type": "list"},
        })
    return out


RE_PG = re.compile(r'href="([^"]*vod/type/id/\d+/page/(\d+)\.html)"')


class Spider(_Base):
    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        try:
            return any(e in str(url).lower() for e in
                       ('.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.webm'))
        except Exception:
            return False

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        cfg = {}
        ex = (extend or "").strip()
        if ex.startswith("{"):
            try:
                cfg = json.loads(ex)
            except Exception:
                cfg = {}
        elif ex:
            cfg = {"host": ex.rstrip("/")}
        self.host = (cfg.get("host") or "").rstrip("/")
        self.site = cfg.get("site") or SITE

    def getName(self):
        return self.site

    # ---------------------------------------------------------------- 首页
    def homeContent(self, filter):
        cls = [{"type_id": t, "type_name": n} for t, n in CLS]
        return {"class": cls}

    def homeVideoContent(self):
        vid = CLS[5][0]
        try:
            h = _get(self, "%s/type/id/%s/page/1.html" % (WEB, vid))
            lst = _cards(self, h)
            return {"list": lst[:30]}
        except Exception:
            return {"list": []}

    # ---------------------------------------------------------------- 分类
    def categoryContent(self, tid, pg, filter, extend):
        try:
            p = max(1, int(pg))
            h = _get(self, "%s/type/id/%s/page/%d.html" % (WEB, tid, p))
            lst = _cards(self, h)
            pc = 1
            ps = [int(x[1]) for x in RE_PG.findall(h)]
            if ps:
                pc = max(ps)
                if p >= pc:
                    pc = p
            if not lst:
                pc = p          # 空页刹车，不许无限翻
            return {"page": p, "pagecount": pc, "limit": max(60, len(lst)),
                    "total": 0, "list": lst}
        except Exception:
            print("[xmhjs] category错误:", traceback.format_exc()[-300:])
            return {"page": 1, "pagecount": 1, "limit": 0, "total": 0, "list": []}

    # ---------------------------------------------------------------- 详情
    def detailContent(self, ids):
        try:
            raw = ids[0] if isinstance(ids, (list, tuple)) else ids
            vid, sid, nid, title, pic = _parseid(raw)
            if not vid:
                return {"list": []}
            purl = "%s/play/id/%s/sid/%s/nid/%s.html" % (WEB, vid, sid or "1", nid or "1")
            if not title:
                try:
                    h = _get(self, "%s/play/id/%s/sid/%s/nid/%s.html" % (WEB, vid, sid, nid))
                    t = re.search(r'class="[^"]*\btitle\b[^"]*"[^>]*>([^<]{2,120})', h)
                    title = _clean(t.group(1)) if t else vid
                    im = re.search(r'<img[^>]+src="(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', h)
                    pic = im.group(1) if im else ""
                except Exception:
                    pass
            play = "%s%s" % (self.host or HOSTS[0], purl)
            vod = {
                "vod_id": raw if isinstance(raw, str) else raw[0],
                "vod_name": (title or vid)[:120],
                "vod_pic": pic,
                "vod_remarks": "正片",
                "vod_content": "站方无详情页（点击直达播放页）；播放地址由播放页实时解析。",
                "vod_play_from": "%s·直连$$$%s·嗅探" % (self.site, self.site),
                "vod_play_url": "正片$%s$$$正片$%s@@sniff" % (play, purl),
            }
            return {"list": [vod]}
        except Exception:
            print("[xmhjs] detail错误:", traceback.format_exc()[-300:])
            return {"list": []}

    # ---------------------------------------------------------------- 搜索
    def searchContent(self, key, quick, pg="1"):
        try:
            from urllib.parse import quote
        except Exception:
            from urllib import quote
        p = max(1, int(pg or 1))
        try:
            h = _get(self, "%s/search/page/%d.html?wd=%s" % (WEB, p, quote(key)))
            lst = _cards(self, h)
            pc = p + 1 if len(lst) >= 50 else p
            return {"list": lst, "page": p, "pagecount": pc,
                    "limit": 60, "total": 0}
        except Exception:
            print("[xmhjs] search错误:", traceback.format_exc()[-300:])
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}

    # ---------------------------------------------------------------- 播放
    def playerContent(self, flag, id, vipFlags):
        try:
            raw = str(id or "")
            sniff = raw.endswith("@@sniff")
            purl = raw[:-len("@@sniff")] if sniff else raw
            if purl.startswith("http"):
                url = purl
                if sniff:
                    return {"parse": 0, "url": url, "header": dict(_headers())}
                m = re.search(r"/play/id/(\d+)/sid/(\d+)/nid/(\d+)", url)
                if m:
                    vid, sid, nid = m.groups()
                    url = "%s%s/play/id/%s/sid/%s/nid/%s.html" % (
                        self.host or HOSTS[0], WEB, vid, sid, nid)
            else:
                url = (self.host or HOSTS[0]) + purl
            base = url.rsplit("/", 2)[0]
            h = _get(self, url if url.startswith("/") else url[len(self.host or HOSTS[0]):],
                     referer=base + "/", nocache=True)
            m = re.search(r'var\s+player_data\s*=\s*(\{.*?\})\s*</script>', h or "", re.S)
            if m:
                try:
                    d = json.loads(m.group(1).replace("\\/", "/"))
                except Exception:
                    d = {}
                u = (d.get("url") or "").strip()
                enc = d.get("encrypt", 0)
                if u and not sniff:
                    if u.startswith("//"):
                        u = "https:" + u
                    if "m3u8" in u or self.isVideoFormat(u) or enc == 0:
                        return {"parse": 0, "url": u, "header": dict(_headers())}
                if sniff:
                    return {"parse": 0, "url": url, "header": dict(_headers())}
            # 兜底：多正则抠流
            for rx in (r'"url":"(https?:[^"\\]+m3u8[^"]*)"',
                       r'(https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*)',
                       r'["\']file["\']\s*:\s*["\']([^"\']+)["\']'):
                mm = re.search(rx, h or "")
                if mm and not sniff:
                    u = mm.group(1).replace("\\/", "/")
                    return {"parse": 0, "url": u, "header": dict(_headers())}
            return {"parse": 0, "url": url, "header": dict(_headers())}
        except Exception:
            print("[xmhjs] player错误:", traceback.format_exc()[-300:])
            return {"parse": 0, "url": "", "header": dict(_headers())}

    def liveContent(self, url):
        return ""

    def localProxy(self, param=None):
        return [404, "text/plain", b"", {}]

    def destroy(self):
        pass