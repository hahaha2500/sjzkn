# -*- coding: utf-8 -*-
"""
麻豆传媒AI (madouai.xyz) —— TVBox/FongMi type=3 Python 源（默影视兼容·提速版）

★本版针对「海报加载慢」做了实测优化：
  瓶颈实测：列表页海报走站方 /api/v1/image/proxy，单张 1~3.8MB，平均 12 秒/张
  真凶定位：站方 image/proxy 支持【未被前端使用的缩略参数 size=small】
            实测 2135245 字节 -> 7748 字节（1/276），且三种 path 前缀(jhimage/image/jpe)全部生效
  本版改动：
    1) 列表/搜索/首页 海报加 &size=small   -> 流量降 100 倍
    2) 详情页仍用原图                      -> 保持大图清晰
    3) localProxy 加 LRU 磁盘无关内存缓存  -> 重复浏览秒回，不再重下
    4) 图片超时独立收紧(12s)，不拖累接口
    5) 列表接口 TTL 缓存 90s                -> 翻页来回不重复请求
    6) 首页条数 24 -> 18                   -> 首屏图片请求量再降
  ★若发现缩略图参数被站方改掉，extend 传 {"thumb":"large"} 即可回到原图

站点结构(全实测)：
  页面类型 Vue3+Vite SPA，HTML 仅 1105 字节空壳 -> 必须走 JSON 接口
  接口     baseURL=/api/v1，信封 {"code":200,"message":"ok","data":...}
  反爬     无 CF / 无签名 / 无 JS 加密，裸请求也 200
  播放     /api/v1/m3u8/proxy?path=<videoUrl> 直吐 m3u8(AES-128 KEY 在清单内明文)
坑：
  1) 列表接口搜索参数名被【静默忽略】返回全量首页，真搜索是 /videos/search?q=
  2) coverUrl 两种形态：带 /api/ 前缀 或 裸相对路径(后者直取 404，必须转交代理)
  3) m3u8 分片带 ?auth_key= 限时签名 -> 播放现取不缓存
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
IMG_TIMEOUT = 12          # 图片单独收紧，别拖累接口
LIST_TTL = 90             # 列表接口缓存秒数
IMG_CACHE_MAX = 120       # localProxy 图片缓存条数上限
IMG_CACHE_MAX_BYTES = 48 * 1024 * 1024

# 模块级缓存：壳子可能每次调用都 new Spider，实例级缓存会全废
_M_CACHE = {}             # 接口缓存 {key:(ts,data)}
_M_IMG = {}               # 图片缓存 {url:(ts,bytes)}
_M_IMG_ORDER = []         # LRU 顺序
_M_IMG_BYTES = [0]
_M_LOCK = threading.Lock()
_M_PREFETCH = set()


class Spider(BaseSpider):

    # ------------------------------------------------- 加载层(默影视铁律)

    def getDependence(self):
        # 必须空列表。填 ["requests"] 会让壳子装依赖失败 -> 整源白屏
        return []

    def getName(self):
        return "麻豆传媒AI"

    def __init__(self):
        super().__init__()
        self.host = HOSTS[0]
        self.proxies = {}
        self.prefix = ""
        self.timeout = TIMEOUT
        self.home_count = 18       # 首屏条数（海报慢的主因之一）
        self.thumb = "small"       # 列表海报用的 size 参数，""=原图
        self._last = 0.0
        self._opener = None
        self._img_opener = None

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
        # 缩略图档位：small(实测最省) / medium / large(原图) / ""=不带参数=原图
        self.thumb = str(ext.get("thumb", "small"))
        px = str(ext.get("proxy") or "").strip()
        self.proxies = {"http": px, "https": px} if px else {}
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(self.proxies))
        self._img_opener = self._opener

    # ------------------------------------------------- 网络

    def _url(self, path):
        url = path if path.startswith("http") else self.host + path
        if self.prefix:
            url = self.prefix + "/" + url
        return url

    def _raw(self, url, binary=False, timeout=None, referer=None):
        headers = {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if binary:
            # 图片：不要 gzip(压不了反而慢)，短超时
            headers["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
            headers["Accept-Encoding"] = "identity"
        else:
            headers["Accept"] = "application/json, text/plain, */*"
            headers["Accept-Encoding"] = "gzip"
        if referer:
            headers["Referer"] = referer

        opener = self._img_opener if binary else self._opener
        if opener is None:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(self.proxies))
        try:
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout or self.timeout) as resp:
                data = resp.read()
                if not binary and \
                        resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                return data
        except Exception:
            return None if binary else ""

    def _get(self, path, binary=False, referer=None, timeout=None):
        gap = time.time() - self._last
        if gap < 0.3:
            time.sleep(0.3 - gap)
        self._last = time.time()
        return self._raw(self._url(path), binary, timeout, referer)

    def _api(self, path, ttl=0, **params):
        """调 JSON 接口，剥掉 {code,message,data} 信封；失败换镜像重试"""
        key = path + "?" + urllib.parse.urlencode(params)
        if ttl:
            hit = _M_CACHE.get(key)
            if hit and (time.time() - hit[0]) < ttl:
                return hit[1]

        data = None
        if params:
            full = path + "?" + urllib.parse.urlencode(params)
        else:
            full = path
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

    # ------------------------------------------------- 海报

    def _cover(self, rel, small=False):
        """coverUrl 两种形态都处理：
             A "/api/v1/image/proxy?path=xxx"  -> 直接拼域名
             B 裸相对路径 "jpe/xxx.jpg"         -> 必须转交 image/proxy，直取 404
        small=True 时追加 &size=small（实测体积降 100 倍）
        """
        if not rel:
            return ""
        if rel.startswith("http"):
            base = rel
        elif rel.startswith("/api/"):
            base = self.host + rel
        else:
            bare = rel.lstrip("/")
            base = "%s%s/image/proxy?path=%s" % (
                self.host, API, urllib.parse.quote(bare, safe=""))
        if small and self.thumb:
            base += "&size=" + self.thumb
        return base

    def _warm(self, urls):
        """后台预热海报：列表先返回，图片边下边显示，不阻塞 UI"""
        urls = [u for u in urls if u]
        if not urls:
            return

        def run(target):
            for u in target:
                with _M_LOCK:
                    if u in _M_IMG:
                        continue
                    if u in _M_PREFETCH:
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

    @staticmethod
    def _put_img(url, data):
        with _M_LOCK:
            if url in _M_IMG:
                return
            _M_IMG[url] = (time.time(), data)
            _M_IMG_ORDER.append(url)
            _M_IMG_BYTES[0] += len(data)
            while (_M_IMG_ORDER and
                   (len(_M_IMG_ORDER) > IMG_CACHE_MAX or
                    _M_IMG_BYTES[0] > IMG_CACHE_MAX_BYTES)):
                old = _M_IMG_ORDER.pop(0)
                it = _M_IMG.pop(old, None)
                if it:
                    _M_IMG_BYTES[0] -= len(it[1])

    def _img(self, path):
        """按路径取图（带模块级缓存）。path 形如 /api/v1/image/proxy?..."""
        url = self._url(path)
        with _M_LOCK:
            hit = _M_IMG.get(url)
            if hit:
                _M_IMG_ORDER.remove(url)
                _M_IMG_ORDER.append(url)
                return hit[1]
        data = self._raw(url, binary=True, timeout=IMG_TIMEOUT)
        if data:
            self._put_img(url, data)
        return data

    # ------------------------------------------------- 字段

    def _clean(self, s):
        return (s or "").replace("$", "").replace("#", "").strip()

    def _item(self, v, small=True):
        pic = self._cover(v.get("coverUrl"), small)
        dur = v.get("durationSec") or 0
        remark = " ".join(x for x in [
            v.get("categoryName") or "",
            ("%d分钟" % (dur // 60)) if dur else "",
        ] if x)
        return {
            "vod_id": str(v.get("id")),
            "vod_name": self._clean(v.get("title")) or "未命名",
            "vod_pic": pic,
            "vod_remarks": remark,
        }

    @staticmethod
    def _empty(page=1):
        return {"list": [], "page": page, "pagecount": 1,
                "limit": 24, "total": 0}

    # ------------------------------------------------- 六接口

    def homeContent(self, filter=False):
        data = self._api("/categories", ttl=600)
        classes = []
        if data:
            for c in data:
                if c.get("enabled") and c.get("type") == "video":
                    classes.append({"type_id": str(c["id"]),
                                    "type_name": self._clean(c["name"])})
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
        items = []
        if data:
            for v in data.get("items", []):
                items.append(self._item(v, small=True))
        self._warm([i["vod_pic"] for i in items])
        return {"list": items} if items else self._empty()

    def categoryContent(self, tid, pg, filter=False, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        data = self._api("/videos", ttl=LIST_TTL,
                         categoryId=str(tid or ""), page=pg, page_size=24)
        if not data:
            return self._empty(pg)
        items = [self._item(v, small=True) for v in data.get("items", [])]
        self._warm([i["vod_pic"] for i in items])
        return {"list": items,
                "page": data.get("page", pg),
                "pagecount": max(1, int(data.get("totalPages") or 1)),
                "limit": data.get("size", 24),
                "total": data.get("total", 0)}

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

        return {"list": [{
            "vod_id": vid,
            "vod_name": self._clean(d.get("title")) or "未命名",
            # 详情用原图(不加 size)，保持大图清晰；顺手预热
            "vod_pic": self._cover(d.get("coverUrl"), small=False),
            "vod_remarks": mark,
            "vod_year": (d.get("publishedAt") or "")[:4],
            "vod_area": d.get("categoryName") or "",
            "vod_actor": d.get("authorName") or "",
            "vod_content": (d.get("description") or "").strip() or meta,
            # 双线路：直连(m3u8 代理) / 嗅探(交壳子)
            "vod_play_from": "麻豆·直连$$$麻豆·嗅探",
            "vod_play_url": "m@@%s#s@@%s" % (vid, vid),
        }]}

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

        # 嗅探线路
        if mode == "s":
            return {"parse": 0, "playUrl": self.host + "/media/",
                    "header": header}

        d = self._api("/videos/%s" % vid)
        if not d or not d.get("videoUrl"):
            return {"parse": 0, "playUrl": "", "header": header}
        return {"parse": 0,
                "playUrl": "%s%s/m3u8/proxy?path=%s" % (
                    self.host, API,
                    urllib.parse.quote(d["videoUrl"], safe="")),
                "header": header}

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        # 参数名必须是 q；keyword/name/wd 会被静默忽略返回全量首页
        data = self._api("/videos/search", ttl=60,
                         q=key, page=pg, page_size=24)
        if not data:
            return self._empty(pg)
        items = [self._item(v, small=True) for v in data.get("items", [])]
        self._warm([i["vod_pic"] for i in items])
        return {"list": items,
                "page": data.get("page", pg),
                "pagecount": max(1, int(data.get("totalPages") or 1)),
                "limit": data.get("size", 24),
                "total": data.get("total", 0)}

    # ------------------------------------------------- 辅助

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
            if isinstance(param, str):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(param).query)
                raw = q.get("url", [""])[0]
            elif isinstance(param, dict):
                v = param.get("url", "")
                raw = v[0] if isinstance(v, (list, tuple)) and v else v
            else:
                raw = ""
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