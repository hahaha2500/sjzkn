# -*- coding: utf-8 -*-
# 骚女汇 vfbai.snnvh82.sbs - TVBox/FongMi type=3
# 基底: 实测海报显示正常的版本; 注入: 重试/契约/localProxy图片兜底/分页真实值
import base64, json, re, threading, time
import urllib.parse, urllib.request, urllib.error, gzip, zlib
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        pass

CATS = [{"type_id": "9500846", "type_name": "精品推荐"},
        {"type_id": "9510846", "type_name": "国产传媒"},
        {"type_id": "9520846", "type_name": "探花系列"},
        {"type_id": "9530846", "type_name": "偷拍自拍"},
        {"type_id": "9540846", "type_name": "熟女少妇"},
        {"type_id": "9550846", "type_name": "无码专区"},
        {"type_id": "9560846", "type_name": "欧美性爱"},
        {"type_id": "9570846", "type_name": "颜值正义"},
        {"type_id": "9600856", "type_name": "美乳巨乳"},
        {"type_id": "9610856", "type_name": "网曝事件"},
        {"type_id": "9620856", "type_name": "国产主播"},
        {"type_id": "9630856", "type_name": "中文字幕"},
        {"type_id": "9640856", "type_name": "制服丝袜"},
        {"type_id": "9650856", "type_name": "口交自慰"},
        {"type_id": "9660856", "type_name": "国产精品"},
        {"type_id": "9670856", "type_name": "大秀视频"},
        {"type_id": "9700866", "type_name": "亚洲情色"},
        {"type_id": "9710866", "type_name": "强奸乱伦"},
        {"type_id": "9720866", "type_name": "伦理三级"},
        {"type_id": "9730866", "type_name": "女同性恋"},
        {"type_id": "9740866", "type_name": "明星换脸"},
        {"type_id": "9750866", "type_name": "AV解说"},
        {"type_id": "9760866", "type_name": "少女萝莉"},
        {"type_id": "9770866", "type_name": "角色剧情"},
        {"type_id": "9800876", "type_name": "精品网红"},
        {"type_id": "9810866", "type_name": "多人群交"},
        {"type_id": "9820866", "type_name": "SM调教"},
        {"type_id": "9830866", "type_name": "动漫卡通"},
        {"type_id": "9840866", "type_name": "变性伪娘"},
        {"type_id": "9850866", "type_name": "VR视角"},
        {"type_id": "9860866", "type_name": "反差母狗"},
        {"type_id": "9870866", "type_name": "人妻系列"}]

BLOCK_CIDS = {"9530846", "9610856", "9710866", "9760866"}
BLOCK_KW = ["小孩子", "门事件"]


class Spider(_BaseSpider):
    HOST = "https://vfbai.snnvh82.sbs"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    _PICS = {}
    PNG_1X1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

    def __init__(self):
        self.host = self.HOST
        self.extend = {}
        self._lock = threading.Lock()
        self._proxy_cache = {"val": None, "at": 0.0}
        try:
            _BaseSpider.__init__(self)
        except Exception:
            pass

    def init(self, extend=""):
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

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        if not url:
            return False
        low = str(url).lower()
        return any(e in low for e in
                   ('.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.mpg', '.webm'))

    def _proxies(self):
        with self._lock:
            if self._proxy_cache["val"] and time.time() - self._proxy_cache["at"] < 120:
                return self._proxy_cache["val"]
        p = self.extend.get("proxy") or "http://127.0.0.1:7890"
        val = {"http": p, "https": p}
        with self._lock:
            self._proxy_cache = {"val": val, "at": time.time()}
        return val

    # 页面请求: 裸连(与实测可用版一致), 失败重试
    def _fetch(self, url, referer=None, timeout=15, img=False, retries=2):
        headers = {"User-Agent": self.UA,
                   "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                   "Accept-Language": "zh-CN,zh;q=0.9",
                   "Accept-Encoding": "gzip, deflate"}
        if referer:
            headers["Referer"] = referer
        if img:
            headers["Referer"] = referer or (self.host + "/")
            headers["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler(self._proxies() if img else {}))
        for i in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers)
                with op.open(req, timeout=timeout) as resp:
                    return self._body(resp.read())
            except Exception:
                if i < retries:
                    time.sleep(0.4)
        return None

    @staticmethod
    def _body(raw):
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
        return raw.decode("utf-8", "replace")

    @staticmethod
    def _d(b64):
        try:
            d = base64.b64decode(b64).decode("utf-8", "replace")
            d = re.sub(r"<span[^>]*>.*?</span>", "", d, flags=re.S)
            d = re.sub(r"<[^>]+>", "", d)
            d = d.replace("&nbsp;", " ").replace("&amp;", "&")
            return re.sub(r"\s+", " ", d).strip()
        except Exception:
            return ""

    @staticmethod
    def _blocked(t):
        low = (t or "").lower()
        return any(k.lower() in low for k in BLOCK_KW)

    # 海报: 原样返回, 零加工 (与实测可用版逐字一致)
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
            title = self._d(b64) if b64 else re.sub(r"\s+", " ", (txt or "")).strip()
            title = title.replace("$", "＄").replace("#", "＃")
            if not title or self._blocked(title):
                continue
            seen.add(vid)
            items.append({"vod_id": vid, "vod_name": title,
                          "vod_pic": pic, "vod_remarks": ""})
        return items

    def _pagecount(self, html):
        m = self.RE_TOTAL.search(html or "")
        if m:
            return int(m.group(2))
        pages = [int(x) for x in re.findall(r"page=(\d+)", html or "")]
        return max(pages) if pages else 1

    def _total(self, html):
        m = self.RE_TOTAL.search(html or "")
        return int(m.group(1)) if m else 0

    def _remember(self, items):
        try:
            for it in items or []:
                if it.get("vod_pic"):
                    self._PICS[it["vod_id"]] = it["vod_pic"]
        except Exception:
            pass

    def homeContent(self, filter=False):
        return {"class": CATS, "list": [], "filters": {}}

    def homeVideoContent(self):
        html = self._fetch(self.host + "/")
        vids = self._parse_list(html)
        self._remember(vids)
        return {"list": vids[:36], "page": 1, "pagecount": 1,
                "limit": min(36, len(vids)), "total": len(vids)}

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or "")
        m = re.search(r"id=(\d+)", tid)
        if m:
            tid = m.group(1)
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        if tid in BLOCK_CIDS:
            return {"list": [], "page": pg, "pagecount": 0, "limit": 0, "total": 0}
        html = self._fetch("%s/list.php?id=%s&page=%d" % (self.host, tid, pg),
                           referer=self.host + "/")
        vids = self._parse_list(html)
        self._remember(vids)
        pc = self._pagecount(html)
        if not vids:
            pc = pg
        return {"list": vids, "page": pg, "pagecount": pc,
                "limit": len(vids), "total": self._total(html)}

    def detailContent(self, ids):
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        url = "%s/video.php?id=%s" % (self.host, vid)
        html = self._fetch(url, referer=self.host + "/")
        play = ""
        if html:
            m = re.search(r"hls\.loadSource\('([^']+)'\)", html)
            if m:
                play = m.group(1)
            if not play:
                m2 = re.search(r"(https?://[^\s'\"\\<>]+\.m3u8[^\s'\"\\<>]*)", html or "")
                if m2:
                    play = m2.group(1).replace("\\/", "/")
        title = ""
        pic = self._PICS.get(vid, "")
        if html:
            tm = re.search(r"document\.title\s*=\s*d\('([^']+)'\)", html)
            if tm:
                title = re.sub(r"^\[[^\]]*\]\s*", "", self._d(tm.group(1))).strip()
            if not pic:
                pm = re.search(r'<img[^>]+src="(https?://cover[^"]+\.jpg)"', html)
                if pm:
                    pic = pm.group(1)
        if not play:
            play = "s0@@" + url
        vod = {"vod_id": vid, "vod_name": title or vid, "vod_pic": pic,
               "vod_play_from": "骚女汇·直连$$$骚女汇·原页",
               "vod_play_url": "正片$%s$$$正片$s0@@%s" % (play, url)}
        return {"list": [vod]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        if self._blocked(key):
            return {"list": [], "page": pg, "pagecount": 1, "limit": 0, "total": 0}
        url = "%s/search.php?content=%s" % (self.host, urllib.parse.quote(str(key)))
        html = self._fetch(url, referer=self.host + "/")
        vids = self._parse_list(html)
        if not vids:
            target = self._redirect(url)
            if target:
                html2 = self._fetch(target, referer=self.host + "/")
                vids = self._parse_list(html2)
        self._remember(vids)
        return {"list": vids, "page": pg, "pagecount": 1,
                "limit": len(vids), "total": len(vids)}

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

    def playerContent(self, flag, ids, vipFlags=None):
        pid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        if pid.startswith("s0@@"):
            return {"parse": 0, "url": pid[4:], "header": {"User-Agent": self.UA}}
        return {"parse": 0, "url": pid,
                "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}

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

            u = urllib.parse.unquote(str(_pv("url")))
            if not u or str(_pv("type")) != "img":
                return [404, "text/plain", b"", {}]
            for ref in (self.host + "/", u.split("/")[0] + "/", None):
                body = self._fetch(u, referer=ref, timeout=12, img=True, retries=1)
                if body:
                    low = u.lower()
                    mime = ("image/png" if low.endswith(".png") else
                            "image/webp" if low.endswith(".webp") else
                            "image/gif" if low.endswith(".gif") else "image/jpeg")
                    return [200, mime, body.encode("utf-8", "ignore"),
                            {"Content-Type": mime, "Cache-Control": "max-age=86400"}]
                if ref is None:
                    break
            return [200, "image/png", self.PNG_1X1,
                    {"Content-Type": "image/png", "Cache-Control": "max-age=600"}]
        except Exception:
            return [404, "text/plain", b"", {}]
