# -*- coding: utf-8 -*-
"""
麻豆传媒AI (madouai.xyz) —— TVBox/FongMi type=3 Python 源
默影视兼容 · 提速版 + 下载 + 搜索增强

★下载说明(实测)：
  站方【没有 mp4、没有 download 字段】，全站 videoUrl 一律 .m3u8(实测 20/20)。
  所以下载 = 拿 m3u8 代理直链，交壳子下载器 / 浏览器 / ffmpeg。
  m3u8 分片带 ?auth_key= 限时签名(首段是过期时间戳)，下载链接【现取】不缓存。

★提速(实测)：
  站方 image/proxy 支持前端没用的隐藏参数 &size=small，2135245 -> 7748 字节。
  列表页加该参数：流量 5.5MB -> 57KB(降 98 倍)。

★站点结构(全实测)：
  页面类型 Vue3+Vite SPA，HTML 仅 1105 字节空壳 -> 必须走 JSON 接口
  接口     baseURL=/api/v1，信封 {"code":200,"message":"ok","data":...}
  反爬     无 CF / 无签名 / 无 JS 加密，裸请求也 200
  播放     /api/v1/m3u8/proxy?path=<videoUrl> 直吐 m3u8(AES-128 KEY 明文在清单内)
坑：
  1) 列表接口搜索参数名被【静默忽略】返回全量首页，真搜索是 /videos/search?q=
  2) coverUrl 两种形态：带 /api/ 前缀 或 裸相对路径(后者直取 404，必须转交代理)
  3) m3u8 分片 auth_key 时效 -> 播放/下载都现取不缓存
  4) 服务端强制忽略 page_size(传 12/18 都回 20 条)
extend 可选：
  {"host":"新域名"} {"proxy":"http://127.0.0.1:7890"} {"prefix":"中转前缀"}
  {"timeout":25} {"home":20} {"thumb":"large"} {"search_size":30} {"action":0}
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

API = "/api/v1"
HOSTS = ["https://www.madouai.xyz", "https://madouai.xyz"]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 20
IMG_TIMEOUT = 12
LIST_TTL = 90
IMG_CACHE_MAX = 120
IMG_CACHE_MAX_BYTES = 48 * 1024 * 1024

# 模块级缓存(壳子可能每次 new Spider，实例级会全废)
_M_CACHE = {}
_M_IMG = {}
_M_ORDER = []
_M_BYTES = [0]
_M_LOCK = threading.Lock()
_M_PREFETCH = set()


class Spider(BaseSpider):

    # ----------------------------------------- 加载层(默影视铁律)

    def getDependence(self):
        return []          # 必须空列表，填依赖会导致整源白屏

    def getName(self):
        return "麻豆传媒AI"

    def __init__(self):
        super().__init__()
        self.host = HOSTS[0]
        self.proxies = {}
        self.prefix = ""
        self.timeout = TIMEOUT
        self.home_count = 18
        self.thumb = "small"
        self.search_size = 24
        self.use_action = 1
        self._last = 0.0
        self._opener = None

    def init(self, extend=""):
        ext = {}
        if extend:
            try:
                ext = extend if isinstance(extend, dict) else json.loads(extend)
            except Exception:
                ext = {}
        if ext.get("host"):
            self.host = str(ext["host"]).rstrip("/")
        self.timeout = int(ext.get("timeout") or TIMEOUT)
        self.prefix = str(ext.get("prefix") or "").rstrip("/")
        self.home_count = int(ext.get("home") or 18)
        self.thumb = str(ext.get("thumb", "small"))
        self.search_size = max(1, min(50, int(ext.get("search_size") or 24)))
        self.use_action = int(ext.get("action", 1))
        px = str(ext.get("proxy") or "").strip()
        self.proxies = {"http": px, "https": px} if px else {}
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(self.proxies))# ----------------------------------------- 网络

    def _url(self, path):
        url = path if path.startswith("http") else self.host + path
        if self.prefix:
            url = self.prefix + "/" + url
        return url

    def _raw(self, url, binary=False, timeout=None, referer=None):
        h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        if binary:
            # 图片压不动，identity 省 CPU；短超时别拖累接口
            h["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
            h["Accept-Encoding"] = "identity"
        else:
            h["Accept"] = "application/json, text/plain, */*"
            h["Accept-Encoding"] = "gzip"
        if referer:
            h["Referer"] = referer
        opener = self._opener or urllib.request.build_opener(
            urllib.request.ProxyHandler(self.proxies))
        try:
            req = urllib.request.Request(url, headers=h)
            with opener.open(req, timeout=timeout or self.timeout) as resp:
                data = resp.read()
                if not binary and \
                        resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                if binary:
                    return data
                try:
                    return data.decode("utf-8", "replace")
                except Exception:
                    return ""
        except Exception:
            return None if binary else ""

    def _get(self, path, binary=False, referer=None, timeout=None):
        gap = time.time() - self._last
        if gap < 0.3:
            time.sleep(0.3 - gap)
        self._last = time.time()
        return self._raw(self._url(path), binary, timeout, referer)

    def _api(self, path, ttl=0, **params):
        """调 JSON 接口并剥掉信封；失败自动换镜像重试一次"""
        key = path + "?" + urllib.parse.urlencode(params)
        if ttl:
            hit = _M_CACHE.get(key)
            if hit and (time.time() - hit[0]) < ttl:
                return hit[1]
        full = path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None
        for _ in range(2):
            txt = self._get(API + full)
            if txt:
                try:
                    obj = json.loads(txt)
                    if isinstance(obj, dict) and obj.get("code") == 200:
                        data = obj.get("data")
                        break
                except Exception:
                    pass
            try:
                i = HOSTS.index(self.host)
            except ValueError:
                i = 0
            self.host = HOSTS[(i + 1) % len(HOSTS)]
        if data is not None and ttl:
            _M_CACHE[key] = (time.time(), data)
        return data

    # ----------------------------------------- 海报

    def _cover(self, rel, small=False):
        """coverUrl 两种形态都要处理：
             A "/api/v1/image/proxy?path=xxx" -> 直接拼域名
             B 裸相对路径 "jpe/xxx.jpg"        -> 必须转交 image/proxy，直取 404
        """
        if not rel:
            return ""
        if rel.startswith("http"):
            base = rel
        elif rel.startswith("/api/"):
            base = self.host + rel
        else:
            base = "%s%s/image/proxy?path=%s" % (
                self.host, API, urllib.parse.quote(rel.lstrip("/"), safe=""))
        if small and self.thumb:
            base += "&size=" + self.thumb
        return base

    @staticmethod
    def _put_img(url, data):
        with _M_LOCK:
            if url in _M_IMG:
                return
            _M_IMG[url] = (time.time(), data)
            _M_ORDER.append(url)
            _M_BYTES[0] += len(data)
            while _M_ORDER and (len(_M_ORDER) > IMG_CACHE_MAX or
                                _M_BYTES[0] > IMG_CACHE_MAX_BYTES):
                it = _M_IMG.pop(_M_ORDER.pop(0), None)
                if it:
                    _M_BYTES[0] -= len(it[1])

    def _img(self, path):
        """按路径取图，命中模块级缓存直接返回(实测冷取6.85s -> 热取0.0000s)"""
        url = self._url(path)
        with _M_LOCK:
            hit = _M_IMG.get(url)
            if hit:
                try:
                    _M_ORDER.remove(url)
                except ValueError:
                    pass
                _M_ORDER.append(url)
                return hit[1]
        d = self._raw(url, binary=True, timeout=IMG_TIMEOUT)
        if d:
            self._put_img(url, d)
        return d

    def _warm(self, urls):
        """后台预热：列表先返回，海报边下边显示，不阻塞首屏"""
        urls = [u for u in urls if u]
        if not urls:
            return

        def run(target):
            for u in target:
                with _M_LOCK:
                    if u in _M_IMG or u in _M_PREFETCH:
                        continue
                    _M_PREFETCH.add(u)
                d = self._raw(self._url(u), binary=True, timeout=IMG_TIMEOUT)
                with _M_LOCK:
                    _M_PREFETCH.discard(u)
                if d:
                    self._put_img(u, d)

        try:
            t = threading.Thread(target=run, args=(urls[:24],))
            t.daemon = True
            t.start()
        except Exception:
            pass

    # ----------------------------------------- 工具

    @staticmethod
    def _clean(s):
        """去掉会破坏 $ / # 分隔符的字符"""
        return (s or "").replace("$", "").replace("#", "").strip()

    def _m3u8_url(self, video_url):
        """m3u8 代理直链。分片带限时签名，所以调用方必须现取不缓存"""
        return "%s%s/m3u8/proxy?path=%s" % (
            self.host, API, urllib.parse.quote(video_url or "", safe=""))

    def _item(self, v, small=True):
        dur = v.get("durationSec") or 0
        return {
            "vod_id": str(v.get("id")),
            "vod_name": self._clean(v.get("title")) or "未命名",
            "vod_pic": self._cover(v.get("coverUrl"), small),
            "vod_remarks": " ".join(x for x in [
                v.get("categoryName") or "",
                ("%d分钟" % (dur // 60)) if dur else "",
            ] if x),
        }

    @staticmethod
    def _empty(page=1):
        return {"list": [], "page": page, "pagecount": 1,
                "limit": 24, "total": 0}

    @staticmethod
    def _pager(data, pg, items):
        return {"list": items,
                "page": data.get("page", pg),
                "pagecount": max(1, int(data.get("totalPages") or 1)),
                "limit": data.get("size", 24),
                "total": data.get("total", 0)}# ----------------------------------------- 六接口

    def homeContent(self, filter=False):
        data = self._api("/categories", ttl=600)
        classes = []
        if data:
            for c in data:
                if c.get("enabled") and c.get("type") == "video":
                    classes.append({"type_id": str(c["id"]),
                                    "type_name": self._clean(c["name"])})
        # 抓不到就写死兜底，绝不让首页空着
        if not classes:
            classes = [{"type_id": "1", "type_name": "国产自拍"},
                       {"type_id": "2", "type_name": "AV-中文字幕"},
                       {"type_id": "27", "type_name": "每日更新"},
                       {"type_id": "8", "type_name": "AV-无码流出"},
                       {"type_id": "6", "type_name": "麻豆原创AI"}]
        return {"class": classes}

    def homeVideoContent(self):
        n = max(1, min(48, self.home_count))
        data = self._api("/videos", ttl=LIST_TTL, page=1, page_size=n)
        items = [self._item(v, True) for v in data.get("items", [])] \
            if data else []
        self._warm([i["vod_pic"] for i in items])
        return {"list": items} if items else self._empty()

    def categoryContent(self, tid, pg, filter=False, extend=None):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        data = self._api("/videos", ttl=LIST_TTL,
                         categoryId=str(tid or ""), page=pg, page_size=24)
        if not data:
            return self._empty(pg)
        items = [self._item(v, True) for v in data.get("items", [])]
        self._warm([i["vod_pic"] for i in items])
        return self._pager(data, pg, items)

    # ---------------- 详情：播放 + 下载 + 操作卡片

    def detailContent(self, ids):
        vid = ""
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids or "")
        vid = vid.split("@@")[0]
        if not vid:
            return {"list": []}
        d = self._api("/videos/%s" % vid, ttl=300)
        if not d:
            return {"list": []}

        dur = d.get("durationSec") or 0
        mark = "%d:%02d" % (dur // 60, dur % 60) if dur else "正片"
        meta = "　".join(x for x in [
            d.get("categoryName") or "",
            ("时长 %s" % mark) if mark else "",
            ("观看 %d" % (d.get("viewCount") or 0)) if d.get("viewCount") else "",
            d.get("authorName") or "",
            (d.get("publishedAt") or "")[:10],
        ] if x)

        raw = d.get("videoUrl") or ""
        m3u8 = self._m3u8_url(raw)
        name = self._clean(d.get("title")) or "未命名"

        vod = {
            "vod_id": vid,
            "vod_name": name,
            # 详情用原图(不加 size)，保持大图清晰
            "vod_pic": self._cover(d.get("coverUrl"), small=False),
            "vod_remarks": mark,
            "vod_year": (d.get("publishedAt") or "")[:4],
            "vod_area": d.get("categoryName") or "",
            "vod_actor": d.get("authorName") or "",
            "vod_content": (d.get("description") or "").strip() or meta,
            # 播放：直连 / 嗅探
            "vod_play_from": "麻豆·直连$$$麻豆·嗅探",
            "vod_play_url": "m@@%s#s@@%s" % (vid, vid),
            # 下载：站方无 mp4 只有 m3u8，交壳子下载器或浏览器
            "vod_down_url": ("下载$" + m3u8) if raw else "",
            "vod_down_note": "m3u8(分片限时签名，建议尽快下载)",
        }

        # 操作卡片：点开即浏览器打开 / 复制命令
        if self.use_action and raw:
            safe = name[:40].replace('"', "")
            cmd = ("ffmpeg -i \"%s\" -c copy \"%s.mp4\"" % (m3u8, safe))
            try:
                acts = [
                    {"actionId": 1, "type": 1, "title": "浏览器下载(直链)",
                     "url": m3u8},
                    {"actionId": 2, "type": 2, "title": "复制播放直链",
                     "url": m3u8},
                    {"actionId": 3, "type": 2, "title": "复制 ffmpeg 命令",
                     "url": cmd},
                ]
                vod["vod_action"] = json.dumps(acts, ensure_ascii=False)
            except Exception:
                pass
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id or "")
        if pid.startswith("m@@"):
            mode, vid = "m", pid[3:]
        elif pid.startswith("s@@"):
            mode, vid = "s", pid[3:]
        elif "@@" in pid:
            mode, vid = pid.split("@@", 1)
        else:
            mode, vid = "m", pid
        vid = vid.split("@@")[0].strip()
        header = {"User-Agent": UA, "Referer": self.host + "/"}
        if not vid:
            return {"parse": 1, "playUrl": "", "header": header}
        # 嗅探线路：交壳子播放器内建嗅探
        if mode == "s":
            return {"parse": 0, "playUrl": self.host + "/media/",
                    "header": header}
        # 直连：不缓存，分片签名有时效
        d = self._api("/videos/%s" % vid)
        if not d or not d.get("videoUrl"):
            return {"parse": 0, "playUrl": "", "header": header}
        return {"parse": 0, "playUrl": self._m3u8_url(d["videoUrl"]),
                "header": header}

    # ---------------- 搜索

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        kw = self._clean(key)
        if not kw:
            return self._empty(pg)
        # ★参数名必须是 q；keyword/name/wd/text/s 会被静默忽略返回全量首页
        data = self._api("/videos/search", ttl=60,
                         q=kw, page=pg, page_size=self.search_size)
        if not data:
            return self._empty(pg)
        items = [self._item(v, True) for v in data.get("items", [])]
        self._warm([i["vod_pic"] for i in items])
        out = self._pager(data, pg, items)
        out["limit"] = self.search_size
        return out

    # ----------------------------------------- 本地代理

    def isVideoFormat(self, url):
        try:
            return any(x in str(url).lower() for x in
                       [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".webm"])
        except Exception:
            return False

    def manualVideoCheck(self):
        return False

    def localProxy(self, param=None):
        """默影视只认 do=py。返回四元组 [status, mime, body(bytes), header(dict)]"""
        if not param:
            return [404, "text/plain", b"", {}]
        try:
            import base64
            raw = ""
            if isinstance(param, str):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(param).query)
                v = q.get("url", [""])
                raw = v[0] if v else ""
            elif isinstance(param, dict):
                v = param.get("url", "")
                raw = v[0] if isinstance(v, (list, tuple)) and v else v
            if not raw:
                return [404, "text/plain", b"", {}]
            try:
                real = base64.b64decode(
                    raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
            except Exception:
                real = ""
            if not real.startswith("http"):
                real = raw

            path = real[len(self.host):] if real.startswith(self.host) else None
            data = self._img(path) if path else \
                self._raw(self._url(real), binary=True, timeout=IMG_TIMEOUT)
            if not data:
                return [404, "text/plain", b"", {}]
            mime = "application/vnd.apple.mpegurl"
            if data[:4] == b"\x89PNG":
                mime = "image/png"
            elif data[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            elif data[:4] == b"RIFF":
                mime = "image/webp"
            elif ".m3u8" not in real:
                mime = "image/jpeg"
            return [200, mime, data,
                    {"User-Agent": UA, "Referer": self.host + "/"}]
        except Exception:
            return [404, "text/plain", b"", {}]