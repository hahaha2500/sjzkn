# -*- coding: utf-8 -*-
# 骚女汇 vfbai.snnvh82.sbs - TVBox/FongMi type=3
# MacCMS v10 saonvhui 模板; 全站文本 document.write(d(base64)) 混淆, 清洗=删 span 垃圾
import base64, json, re, threading, time
import urllib.parse, urllib.request, urllib.error, gzip, zlib
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        pass

class Spider(_BaseSpider):
    HOSTS = ["https://vfbai.snnvh82.sbs"]
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    # 内容屏蔽：偷拍自拍 / 网曝事件 / 强奸乱伦 / 少女萝莉 四类不进目录
    BLOCK_CIDS = {"9530846", "9610856", "9710866", "9760866"}
    BLOCK_KW = ("小孩子", "门事件")
    _PICS = {}
    PNG_1X1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

    def __init__(self):
        self.host = self.HOSTS[0]
        self.extend = {}
        self._lock = threading.Lock()
        self._proxy_cache = {"val": None, "at": 0.0}
        try:
            _BaseSpider.__init__(self)
        except Exception:
            pass

    def init(self, extend=""):
        # 壳在Loader 里 obj.put("siteKey", siteKey)，这里做兜底读取
        self.siteKey = str(getattr(self, "siteKey", "") or self.extend.get("sitekey", "") or "")
        self.extend = self._parse_extend(extend)
        h = self.extend.get("host")
        if h:
            h = str(h).rstrip("/")
            if h.startswith("//"):
                h = "https:" + h
            elif not h.startswith("http"):
                h = "https://" + h
            self.host = h

    @staticmethod
    def _parse_extend(extend):
        out = {}
        if not extend:
            return out
        if isinstance(extend, dict):
            out.update(extend)
        else:
            s = str(extend).strip()
            if s.startswith("{"):
                try:
                    out.update(json.loads(s))
                except Exception:
                    pass
            elif s.startswith("http"):
                out["host"] = s
        return out

    def getName(self):
        return self.extend.get("name") or "骚女汇"

    def isVideoFormat(self, url):
        if not url:
            return False
        low = str(url).lower()
        return any(e in low for e in
                   ('.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.mpg', '.webm'))

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def _proxy_val(self):
        """显式配置的代理；没配返回 None（直连）"""
        p = self.extend.get("proxy")
        if not p:
            p = self.extend.get("proxy_base")
        if not p:
            return None
        p = str(p).strip()
        if not p.startswith("http"):
            p = "http://" + p
        return p

    def _proxy_candidates(self):
        """取图代理候选链：显式配置优先，其次探测本机常见代理端口。
        全部失败 -> 空 dict = 直连（绝不强制代理）"""
        out = []
        p = self._proxy_val()
        if p:
            out.append(p)
        for port in (7890, 7891, 1080, 1087, 10809, 24780, 2333, 6153, 16666, 20170, 9090, 7070):
            cand = "http://127.0.0.1:%d" % port
            if cand not in out:
                out.append(cand)
        return out[:4]          # 只探 4 个，避免整页卡死

    def _open(self, url, headers, timeout, proxies, raw=False):
        """raw=True 返回原始 bytes（取图必须走这里：图片是二进制，
        经 utf-8 decode/encode 往返会被破坏 -> 壳子拿到坏图显示不出来）"""
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies if proxies else {}))
        req = urllib.request.Request(url, headers=headers)
        with op.open(req, timeout=timeout) as resp:
            data = resp.read()
            if raw:
                return data
            return self._body(data, resp.headers.get("Content-Encoding"))

    def _fetch(self, url, referer=None, timeout=15, img=False, retries=2):
        headers = {
            "User-Agent": self.UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate"}
        if referer:
            headers["Referer"] = referer
        if img:
            headers["Referer"] = referer or (self.host + "/")
            headers["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
            # ★ 直连优先，失败再降级代理（绝不强制代理，否则不开代理的机器全白屏）
            cands = [{}] + [{"http": p, "https": p} for p in self._proxy_candidates()]
            for n, proxies in enumerate(cands):
                tmo = timeout if n == 0 else min(timeout, 5)   # 直连给足时间，代理快速试探
                for i in range(retries + 1):
                    try:
                        r = self._open(url, headers, tmo, proxies, raw=True)
                        if r and len(r) > 128:
                            return r
                    except Exception:
                        pass
                    if i < retries:
                        time.sleep(0.3)
            return None
        for i in range(retries + 1):
            try:
                return self._open(url, headers, timeout, {})
            except Exception:
                if i < retries:
                    time.sleep(0.4)
        return None

    @staticmethod
    def _body(raw, enc):
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif raw[:2] == b"\x78":
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass
        return raw.decode("utf-8", "ignore")

    def _dec(self, b64):
        try:
            s = base64.b64decode(b64).decode("utf-8", "ignore")
        except Exception:
            return ""
        s = re.sub(r"<span[^>]*>.*?</span>", "", s, flags=re.S)
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&")
        return re.sub(r"\s+", " ", s).strip()

    def _blocked(self, title):
        t = title or ""
        for kw in self.BLOCK_KW:
            if kw in t:
                return True
        return False

    @staticmethod
    def _clean(t):
        t = (t or "").replace("$", "＄").replace("#", "＃")
        return re.sub(r"\s+", " ", t).strip()

    RE_CAT = re.compile(
        r'/list\.php\?id=(\d+)&page=1"?\s*>\s*<script[^>]*>document\.write\(d\(\'([^\']+)\'\)')
    RE_ZONE = re.compile(r'<dt><a href="javascript:;">(.*?)</a></dt>(.*?)</dl>', re.S)

    def _classes(self):
        html = self._fetch(self.host + "/")
        if html:
            out = []
            for _z, body in self.RE_ZONE.findall(html):
                for cid, b64 in self.RE_CAT.findall(body):
                    n = self._dec(b64)
                    if n:
                        out.append({"type_id": cid, "type_name": n})
            out = [c for c in out if c["type_id"] not in self.BLOCK_CIDS]
            if out:
                return out
        return [{"type_id": "9500846", "type_name": "精品推荐"},
                {"type_id": "9540846", "type_name": "熟女少妇"},
                {"type_id": "9550846", "type_name": "无码专区"},
                {"type_id": "9560846", "type_name": "欧美性爱"},
                {"type_id": "9730866", "type_name": "女同性恋"}]

    RE_CARD = re.compile(
        r'<a class="thumbnail" href="/video\.php\?id=(\d+)"[^>]*>'
        r'<img[^>]+src="([^"]+)"[^>]*>.*?</a>\s*<div class="video-info">\s*<h5>'
        r'\s*<a href="/video\.php\?id=\d+"[^>]*>'
        r'(?:<script[^>]*>document\.write\(d\(\'([^\']+)\'\)|([^<]*))', re.S)
    RE_TOTAL = re.compile(r'共(\d+)条数据,当前\d+/(\d+)页')
    RE_LAST = re.compile(r'page=(\d+)"[^>]*>\s*尾页')

    def _parse_list(self, html):
        items, seen = [], set()
        if not html:
            return items
        for vid, pic, b64, txt in self.RE_CARD.findall(html):
            if vid in seen:
                continue
            name = self._dec(b64) if b64 else self._clean(txt)
            if not name:
                continue
            name = self._clean(name)
            if self._blocked(name):
                continue
            seen.add(vid)
            items.append({"vod_id": vid, "vod_name": name,
                          "vod_pic": self._pic(pic), "vod_remarks": "正片"})
        return items

    def _count(self, html):
        m = self.RE_TOTAL.search(html or "")
        if m:
            return int(m.group(1)), int(m.group(2))
        m2 = self.RE_LAST.search(html or "")
        return 0, (int(m2.group(1)) if m2 else 1)

    def _pic(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            url = self.host + "/" + url.lstrip("/")
        mode = str(self.extend.get("img_retry", "0"))
        if mode == "2":
            return self._proxy_img(url)
        if mode == "1" and "/cover0" not in url:
            return self._proxy_img(url)
        return url

    def _proxy_img(self, url):
        b = base64.b64encode(url.encode("utf-8")).decode("ascii")
        b = urllib.parse.quote(b, safe="")
        # ★ BaseLoader.proxy: params 里有 siteKey 就走 getSpider(siteKey) 分支，
        #   不依赖 PyLoader.recent（recent==null 会直接返回 null → Invalid proxy response）
        sk = self.siteKey
        if sk:
            return "%s?do=py&siteKey=%s&type=img&url=%s" % (self._proxy_base(), sk, b)
        return "%s?do=py&type=img&url=%s" % (self._proxy_base(), b)

    def _proxy_base(self):
        pb = self.extend.get("proxy_base")
        if pb:
            return str(pb).rstrip("/")
        try:
            from com.github.catvod import Proxy
            u = Proxy.getUrl(True)
            if u:
                return str(u).split("?")[0]
        except Exception:
            pass
        return "http://127.0.0.1:9978"

    def _remember(self, items):
        try:
            for it in items or []:
                if it.get("vod_pic"):
                    self._PICS[it["vod_id"]] = it["vod_pic"]
        except Exception:
            pass

    def homeContent(self, filter=False):
        cls = self._classes()
        html = self._fetch(self.host + "/")
        vod = self._parse_list(html)[:36]
        self._remember(vod)
        return {"class": cls, "vod": vod}

    def homeVideoContent(self):
        html = self._fetch(self.host + "/")
        vod = self._parse_list(html)[:36]
        self._remember(vod)
        return {"list": vod, "page": 1, "pagecount": 1,
                "limit": len(vod), "total": len(vod)}

    @staticmethod
    def _norm_tid(tid, extend):
        t = str(tid or "").strip()
        if t.startswith("http"):
            m = re.search(r"id=(\d+)", t)
            return m.group(1) if m else t
        if t.isdigit():
            return t
        if isinstance(extend, dict) and extend.get(t):
            m = re.search(r"(\d{6,})", str(extend[t]))
            return m.group(1) if m else t
        return t

    def categoryContent(self, tid, pg, filter, extend):
        cid = self._norm_tid(tid, extend)
        pg = max(1, int(pg or 1))
        if cid in self.BLOCK_CIDS:
            return {"list": [], "page": pg, "pagecount": 1,
                    "limit": 0, "total": 0}
        url = "%s/list.php?id=%s&page=%d" % (self.host, cid, pg)
        html = self._fetch(url, referer=self.host + "/")
        items = self._parse_list(html)
        total, pc = self._count(html)
        if not items:
            pc = pg
        self._remember(items)
        return {"list": items, "page": pg, "pagecount": max(1, pc),
                "limit": len(items), "total": total}

    def _extract_m3u8(self, html):
        for pat in (r"loadSource\(['\"](https?://[^'\"]+\.m3u8[^'\"]*)",
                    r"src\s*=\s*['\"](https?://[^'\"]+\.m3u8[^'\"]*)",
                    r"(https?://[^\s'\"\\<>]+\.m3u8[^\s'\"\\<>]*)"):
            m = re.search(pat, html)
            if m:
                return m.group(1).replace("\\/", "/").strip()
        return ""

    def detailContent(self, ids):
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        url = "%s/video.php?id=%s" % (self.host, vid)
        html = self._fetch(url, referer=self.host + "/")
        if not html:
            return {"list": [{"vod_id": vid, "vod_name": "加载失败", "vod_pic": "",
                              "vod_play_from": "骚女汇", "vod_play_url": "正片$"}]}
        name = ""
        m = re.search(r"document\.title\s*=\s*d\('([^']+)'\)", html)
        if m:
            name = self._dec(m.group(1))
        name = re.sub(r"^\[[^\]]*\]\s*", "", name).strip()
        if self._blocked(name):
            return {"list": [{"vod_id": vid, "vod_name": "已屏蔽",
                              "vod_pic": "", "vod_play_from": "骚女汇",
                              "vod_play_url": "正片$"}]}
        # 封面四级兜底: 列表登记(本片真图) -> 番号回搜 -> 标题回搜 -> 详情页首图
        pic = self._PICS.get(vid, "")
        if not pic:
            pic = self._find_pic(name, vid, html)
        play = self._extract_m3u8(html) or ("s0@@" + url)
        ep = "正片$%s$$$正片$s0@@%s" % (play, url)
        return {"list": [{"vod_id": vid, "vod_name": name or ("影片 " + vid),
                          "vod_pic": pic, "vod_remarks": "正片",
                          "vod_play_from": "骚女汇·直连$$$骚女汇·原页",
                          "vod_play_url": ep}]}

    RE_CODE = re.compile(r'(?<![A-Za-z0-9])([A-Z]{2,6})[-_]?(\d{2,5})(?!\d)')
    BAD_CODE = ("ID", "MV", "HD", "TV", "3D", "UK", "US", "JP", "CN", "XX", "4K")

    def _find_pic(self, name, vid, html):
        """详情页自身无封面(og/meta/上传目录都没有)，用番号或标题回列表反查本片真图"""
        cands = []
        m = self.RE_CODE.search(name or "")
        if m and m.group(1).upper() not in self.BAD_CODE:
            cands.append(m.group(1).upper() + "-" + m.group(2))
        if name:
            cands.append(name.split(" ")[0][:24])
        for kw in cands[:2]:
            if not kw:
                continue
            url = "%s/search.php?content=%s" % (
                self.host, urllib.parse.quote(kw))
            target = self._redirect(url)
            if not target:
                continue
            m2 = re.search(r"id=(\d+)", target)
            if not m2:
                continue
            lh = self._fetch("%s/list.php?id=%s&page=1" % (
                self.host, m2.group(1)), referer=self.host + "/")
            for it in self._parse_list(lh or ""):
                if it["vod_id"] == vid:
                    return it["vod_pic"]
        # 最后一档: 详情页第一张非模板图(可能是相关推荐，但有图总比空白好)
        for u in re.findall(r'<img[^>]+src="([^"]+)"', html or ""):
            if "/template/" in u or not u.startswith("http"):
                continue
            return self._pic(u)
        return ""

    def playerContent(self, flag, id, vipFlags):
        id = str(id or "")
        if id.startswith("s0@@"):
            return {"parse": 0, "url": id[4:], "header": {"User-Agent": self.UA}}
        if self.isVideoFormat(id):
            return {"parse": 0, "url": id,
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        return {"parse": 1, "url": id, "header": {"User-Agent": self.UA}}

    def _redirect(self, url):
        try:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            op = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
            req = urllib.request.Request(url, headers={"User-Agent": self.UA})
            try:
                op.open(req, timeout=12)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location")
                    if loc:
                        return urllib.parse.urljoin(self.host + "/", loc)
        except Exception:
            return None
        return None

    def searchContent(self, key, quick, pg="1"):
        pg = max(1, int(pg or 1))
        if self._blocked(str(key)):
            return {"list": [], "page": pg, "pagecount": 1, "limit": 0, "total": 0}
        url = "%s/search.php?content=%s" % (self.host, urllib.parse.quote(str(key)))
        target = self._redirect(url)
        if not target:
            return {"list": [], "page": pg, "pagecount": 1, "limit": 0, "total": 0}
        m = re.search(r"id=(\d+)", target)
        if not m:
            html = self._fetch(target, referer=self.host + "/")
            items = self._parse_list(html)
            self._remember(items)
            return {"list": items, "page": 1, "pagecount": 1,
                    "limit": len(items), "total": len(items)}
        return self.categoryContent(m.group(1), pg, False, {})

    def localProxy(self, param=None):
        try:
            if isinstance(param, str):
                raw = param.split("?", 1)[1] if "?" in param else param
                q = urllib.parse.parse_qs(raw)
            else:
                q = param if isinstance(param, dict) else None
                if q is None:
                    return [404, "text/plain", b"", {}]

            def _pv(key):
                v = q.get(key)
                if isinstance(v, list):
                    v = v[0] if v else ""
                return v or ""

            u = _pv("url")
            if not u:
                return [404, "text/plain", b"", {}]
            u = urllib.parse.unquote(str(u))
            if str(_pv("type")) != "img":
                return [404, "text/plain", b"", {}]
            for ref in (self.host + "/", u.split("/")[0] + "/", None):
                body = self._fetch(u, referer=ref, timeout=8, img=True, retries=0)
                if body and len(body) > 128 and body[:1] not in (b"<", b" "):
                    # 按魔数判 mime，不靠后缀（图床常无扩展名）
                    mime = "image/jpeg"
                    if body[:8] == b"\x89PNG\r\n\x1a\n":
                        mime = "image/png"
                    elif body[:6] in (b"GIF87a", b"GIF89a"):
                        mime = "image/gif"
                    elif body[:4] == b"RIFF" and body[8:12] == b"WEBP":
                        mime = "image/webp"
                    return [200, mime, body,
                            {"Content-Type": mime, "Cache-Control": "max-age=86400"}]
                if ref is None:
                    break
            return [200, "image/png", self.PNG_1X1,
                    {"Content-Type": "image/png", "Cache-Control": "max-age=600"}]
        except Exception:
            return [404, "text/plain", b"", {}]
