# -*- coding: utf-8 -*-
# =====================================================================
# aeete.com (Auete影视网)  TVBox·FongMi type=3 Python 点播源
# 站点：Auete 自写模板 —— 无采集接口、搜索页图形验证码（不硬破，走本地搜索兜底）
# URL 规律（实测 2026-09-28）：
#   分类   /Movie/ /Tv/ /Zy/ /Dm/ /qita/     tid: 1电影 2电视剧 3综艺 4动漫 5其他
#   分页   第1页 /{C}/index.html   第2页起 /{C}/index{pg}.html （index0=404）
#   详情   /{C}/{子类}/{slug}/      卡片 li[data-href] + img + h2
#   播放   /{C}/{子类}/{slug}/play-{sid}-{nid}.html
#   播放流 播放页 var now=base64decode("...") → 解出 m3u8 直链
#   搜索   /auete4so.php?searchword= 有验证码 → 本地搜索（今日更新/热播/分类页标题匹配）
# 加载层铁律（对齐默影视实测能出片的 jook/77dpw4）：
#   getDependence()→[] / 显式 __init__ 先调基类 / BaseSpider 别名 / localProxy 默认参
# =====================================================================
import re
import json
import base64
import time

try:
    from urllib.parse import quote, unquote, urljoin
except Exception:
    from urllib import quote, urljoin
    unquote = quote

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self, *a, **k):
            pass

try:
    import requests as rq
    from requests.adapters import HTTPAdapter
    _HAS_RQ = True
except Exception:
    _HAS_RQ = False
    import urllib.request as urllib_request
    import urllib.error as urllib_error

# ★ 站内搜索验证码=算术题(4+2=?)，ddddocr OCR 自动解（可选依赖，缺失则回落本地池）
try:
    import ddddocr as _ddddocr
    _HAS_OCR = True
except Exception:
    _ddddocr = None
    _HAS_OCR = False


class Spider(BaseSpider):

    NAME = "Auete"
    HOST = "https://www.aeete.com"
    UA = ("Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

    # 分类：前缀即 URL 路径段
    CATS = [
        {"id": "1", "name": "电影", "path": "Movie"},
        {"id": "2", "name": "电视剧", "path": "Tv"},
        {"id": "3", "name": "综艺", "path": "Zy"},
        {"id": "4", "name": "动漫", "path": "Dm"},
        {"id": "5", "name": "其他", "path": "qita"},
    ]
    PATH2ID = {c["path"].lower(): c["id"] for c in CATS}
    ID2PATH = {c["id"]: c["path"] for c in CATS}

    # ---------------- 加载层（默影视铁律，一行不能少） ----------------
    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = self.HOST
        self.session = None
        self._cache = {}
        self._seen = {}      # tid -> {首卡id: 页码} 分页刹车
        self._last_err = ""
        # 搜索破解状态
        self._so_sess = None       # 搜索专用会话(验证码cookie绑定)
        self._so_ocr = None
        self._so_ok = False        # 会话已放行
        self._so_ts = 0            # 上次搜索时间(限10秒一次)
        self._so_last = {}         # kw -> results 短缓存

    def init(self, extend=""):
        try:
            conf = {}
            if isinstance(extend, dict):
                conf = dict(extend)
            elif isinstance(extend, str) and extend.strip():
                s = extend.strip()
                if s.startswith("{"):
                    try:
                        conf = json.loads(s)
                    except ValueError:
                        conf = {}
                elif s.startswith("http"):
                    conf = {"host": s}
            h = str(conf.get("host") or conf.get("url") or "").strip().rstrip("/")
            if h:
                if not h.startswith("http"):
                    h = "https://" + h
                self.host = h
        except Exception:
            pass

    def getName(self):
        return self.NAME

    def getDependence(self):
        # ★ 默影视铁律：[] 不给宿主加依赖负担（requests 已 try import 自兜底）
        return []

    def isVideoFormat(self, url):
        u = str(url or "").lower().split("?")[0]
        return any(u.endswith(e) for e in
                   [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    # ---------------- 网络单入口 ----------------
    def _ensure(self):
        if not getattr(self, "host", None):
            self.host = self.HOST
        if getattr(self, "_cache", None) is None:
            self._cache = {}
        if not hasattr(self, "_seen"):
            self._seen = {}
        if not hasattr(self, "_last_err"):
            self._last_err = ""
        if _HAS_RQ and getattr(self, "session", None) is None:
            self.session = rq.Session()
            try:
                ad = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
                self.session.mount("http://", ad)
                self.session.mount("https://", ad)
            except Exception:
                pass
            self.session.headers.update({
                "User-Agent": self.UA,
                "Referer": self.host + "/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })

    def _get(self, path, timeout=12, cache=0, abs_url=None):
        self._ensure()
        url = abs_url or (self.host + path)
        if cache:
            hit = self._cache.get(url)
            if hit and time.time() - hit[0] < cache:
                return hit[1]
        text = ""
        try:
            if _HAS_RQ:
                r = self.session.get(url, timeout=timeout)
                # ★ 响应头不带 charset → 必须显式按 utf-8 解码，否则 requests
                #   用 ISO-8859-1 解出乱码，中文标题全废（搜索/详情连坐）
                r.encoding = r.apparent_encoding or "utf-8"
                try:
                    r.encoding = "utf-8"
                    text = r.text or ""
                except Exception:
                    text = (r.content or b"").decode("utf-8", "ignore")
                if r.status_code >= 400:
                    self._last_err = "HTTP {}".format(r.status_code)
            else:
                req = urllib_request.Request(url, headers={
                    "User-Agent": self.UA, "Referer": self.host + "/"})
                with urllib_request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                text = ""
                for enc in ("utf-8", "gbk"):
                    try:
                        text = raw.decode(enc)
                        break
                    except Exception:
                        continue
        except Exception as e:
            self._last_err = "{}: {}".format(type(e).__name__, str(e)[:100])
            return ""
        if cache and text:
            self._cache[url] = (time.time(), text)
        return text

    # ---------------- 解析工具 ----------------
    @staticmethod
    def _clean(s):
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", str(s))
        s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&ldquo;", "“").replace("&rdquo;", "”"))
        s = s.replace("$", " ").replace("#", " ").strip()
        return s

    def _parse_cards(self, html, limit=36):
        """列表卡：li[data-href] → img + h2 + 备注(hdtag/rating)"""
        out, seen = [], set()
        if not html:
            return out
        for m in re.finditer(
                r'<li[^>]*data-href="([^"]+)"[^>]*>(.*?)</li>', html, re.S):
            path, body = m.group(1), m.group(2)
            if not re.match(r"^/[A-Za-z]+/[^/]+/[^/]+/$", path):
                continue
            am = re.search(r'<img[^>]+src="([^"]+)"[^>]*>', body)
            nm = re.search(r'alt="([^"]+)"', body) or re.search(r'title="([^"]+)"', body)
            if not nm:
                hm = re.search(r'<h2><a[^>]*title="([^"]+)"', body)
                if hm:
                    nm = hm
            if not nm:
                continue
            name = self._clean(nm.group(1))
            if not name or path in seen:
                continue
            pic = am.group(1) if am else ""
            if pic and not pic.startswith("http"):
                pic = urljoin(self.host + "/", pic)
            if pic and any(w in pic.lower() for w in
                           ("loading.gif", "placeholder", "blank", "loading")):
                pic = ""
            note = ""
            tm = re.search(r'class="hdtag"[^>]*>([^<]+)<', body)
            if tm:
                note = self._clean(tm.group(1))
            else:
                rm = re.search(r'([\d.]+)分', body)
                if rm:
                    note = rm.group(1) + "分"
            seen.add(path)
            out.append({"vod_id": path, "vod_name": name,
                        "vod_pic": pic, "vod_remarks": note})
            if len(out) >= limit:
                break
        # 兜底：无 data-href 的普通列表
        if not out:
            for m in re.finditer(
                    r'<a href="(/[A-Za-z]+/[^/]+/[^/]+/)"[^>]*title="([^"]+)"', html):
                path, name = m.group(1), self._clean(m.group(2))
                if path in seen or not name:
                    continue
                seen.add(path)
                out.append({"vod_id": path, "vod_name": name,
                            "vod_pic": "", "vod_remarks": ""})
                if len(out) >= limit:
                    break
        return out

    def _tid_of(self, path):
        seg = path.strip("/").split("/")[0] if path.strip("/") else ""
        return self.PATH2ID.get(seg.lower(), "")

    # ---------------- 五接口 ----------------
    def homeContent(self, filter=False):
        try:
            html = self._get("/", cache=300)
            classes = [{"type_id": c["id"], "type_name": c["name"]} for c in self.CATS]
            lst = self._parse_cards(html, limit=36)
            if not lst:
                lst = self._fallback_list()
            return {"class": classes, "list": lst}
        except Exception as e:
            return {"class": [{"type_id": c["id"], "type_name": c["name"]}
                              for c in self.CATS],
                    "list": [self._diag_card("首页异常",
                                             "{}: {}".format(type(e).__name__, str(e)[:120]))]}

    def homeVideoContent(self):
        try:
            html = self._get("/", cache=300)
            lst = self._parse_cards(html, limit=36)
            if not lst:
                lst = self._fallback_list()
            return {"list": lst}
        except Exception:
            return {"list": []}

    def _fallback_list(self):
        for p in ("/daynew.php", "/dayhot.php", "/Movie/index.html"):
            try:
                items = self._parse_cards(self._get(p, cache=300), limit=36)
                if items:
                    return items
            except Exception:
                continue
        err = getattr(self, "_last_err", "") or "页面解析0条"
        return [self._diag_card("首页抓取失败", err)]

    def _diag_card(self, title, remark):
        return {"vod_id": "/", "vod_name": "[诊断] " + str(title)[:40],
                "vod_pic": "", "vod_remarks": str(remark)[:60],
                "vod_content": str(remark)}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        tid = str(tid or "1").strip()
        # 兼容 tid 传路径/URL
        if not tid.isdigit():
            hit = self._tid_of(tid) or self.PATH2ID.get(tid.lower(), "")
            tid = hit or "1"
        path = self.ID2PATH.get(tid, "Movie")
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        url_path = "/{}/index.html".format(path) if pg <= 1 else \
                   "/{}/index{}.html".format(path, pg)
        html = self._get(url_path, cache=300)
        items = self._parse_cards(html, limit=40)

        if not items:   # 空页刹车
            return {"page": pg, "pagecount": max(pg, 1), "limit": 40,
                    "total": 0, "list": []}
        # 首卡去重刹车（超末页可能404或绕回）
        fid = items[0]["vod_id"]
        seen = self._seen.setdefault(tid, {})
        if pg > 1 and fid in seen:
            return {"page": pg, "pagecount": seen[fid], "limit": 40,
                    "total": 0, "list": []}
        seen.setdefault(fid, pg)
        return {"page": pg, "pagecount": pg + 1, "limit": 40,
                "total": (pg + 1) * 40, "list": items}

    # ================= 站内搜索：验证码 OCR 强力破解 =================
    # 实测(2026-09-28)：
    #   验证码 = 算术题图 "4+2=?" → ddddocr 识别算式 → 提交数字答案
    #   ★ 错1次码即作废(换新图) → 必须一次命中，OCR 循环重拉重试(实测7/8成功,均2.1次)
    #   ★ 破解1次 → 该会话永久放行；唯一限制=搜索10秒1次(超时等10.2s重试)
    #   ★ ddddocr 缺失 → 自动回落本地池(原方案)
    # =================================================================
    def _so_session(self):
        if not _HAS_RQ:
            return None
        if self._so_sess is None:
            self._so_sess = rq.Session()
            try:
                ad = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
                self._so_sess.mount("http://", ad)
                self._so_sess.mount("https://", ad)
            except Exception:
                pass
            self._so_sess.headers.update({
                "User-Agent": self.UA,
                "Referer": self.host + "/",
            })
        return self._so_sess

    @staticmethod
    def _solve_expr(txt):
        """OCR 文本 → 算式答案。容忍噪声(尾巴=2/空格/问号)。解不出返回 None"""
        e = str(txt or "").replace("？", "?").replace(" ", "").replace("，", "")
        m = re.search(r"(\d+)\s*([+\-*×x])\s*(\d+)", e)
        if not m:
            return None
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        if op in ("*", "×", "x"):
            return str(a * b)
        if op == "-":
            return str(a - b)
        return str(a + b)

    def _so_unlock(self, kw):
        """拉验证页+图 → OCR → 提交答案。成功返回结果页 HTML（会话永久放行），失败 False"""
        s = self._so_session()
        if s is None or not _HAS_OCR:
            return False
        if self._so_ocr is None:
            try:
                self._so_ocr = _ddddocr.DdddOcr(show_ad=False)
            except Exception:
                return False
        for _ in range(8):   # 错1次码作废 → 最多8轮重试(实测7/8成功)
            try:
                s.get(self.host + "/auete4so.php?searchword=" + quote(kw), timeout=15)
                img = s.get(self.host + "/include/vdimgck.php", timeout=15).content
                ans = self._solve_expr(self._so_ocr.classification(img))
                if not ans:
                    continue
                r = s.post(
                    self.host + "/auete4so.php?scheckAC=check&page=&searchtype=&order="
                    "&tid=&area=&year=&letter=&yuyan=&state=&money=&ver=&jq="
                    "&searchword=" + quote(kw),
                    data={"validate": ans}, timeout=15)
                if r.text and "验证码不正确" not in r.text:
                    self._so_ok = True
                    self._so_ts = time.time()
                    return r.text   # ★ POST 响应本身就是结果页，直接用
                time.sleep(0.3)
            except Exception:
                time.sleep(0.4)
        return False

    def _search_remote(self, kw):
        """站内搜索。返回 results 列表或 None(不可用)"""
        if not _HAS_RQ or not _HAS_OCR:
            return None
        s = self._so_session()
        if s is None:
            return None
        # 未放行 → 先破解（成功时 POST 响应就是结果页，直接解析）
        if not self._so_ok:
            unlocked = self._so_unlock(kw)
            if not unlocked:
                return None
            items = self._parse_cards(unlocked, limit=50)
            return items if items else []
        # 已放行但撞10秒频率窗 → 立即回落本地池（不干睡等限速）
        if time.time() - self._so_ts < 10.5:
            return None
        try:
            r = s.get(self.host + "/auete4so.php?searchword=" + quote(kw), timeout=15)
            self._so_ts = time.time()
            txt = r.text or ""
            if "安全验证" in txt:        # 会话失效 → 重新破解(下轮)
                self._so_ok = False
                return None
            if "10秒" in txt:            # 撞频率限制 → 回落本地池
                return None
            items = self._parse_cards(txt, limit=50)
            return items if items else []
        except Exception:
            return None

    def searchContent(self, key, quick, pg="1"):
        """优先站内全站搜索(OCR破解验证码) → 失败回落本地池"""
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        kw = str(key or "").strip()
        if not kw:
            return {"page": 1, "pagecount": 1, "limit": 30, "total": 0, "list": []}

        # 第1页：先试站内搜索（结果缓存3分钟，翻页/重复词秒出）
        hits = None
        if pg == 1:
            cached = self._so_last.get(kw)
            if cached and time.time() - cached[0] < 180:
                hits = cached[1]
            if hits is None:
                hits = self._search_remote(kw)
                if hits is not None and hits:
                    self._so_last.clear() if len(self._so_last) > 30 else None
                    self._so_last[kw] = (time.time(), hits)

        if hits is None:   # 站内搜索不可用/失败 → 本地池兜底
            hits = self._search_local(kw)
        elif not hits and pg == 1:
            # 站内搜索通了但真没结果 → 不回落（避免词不匹配的假阳性）
            hits = []

        per = 30
        pagecount = max(1, (len(hits) + per - 1) // per)
        start = (pg - 1) * per
        return {"page": pg, "pagecount": pagecount, "limit": per,
                "total": len(hits), "list": hits[start:start + per]}

    def _search_local(self, kw):
        """本地池：今日更新 + 热播 + 5分类×前15页（5分钟缓存，线程池并行）"""
        pages = ["/daynew.php", "/dayhot.php"]
        for c in ("Movie", "Tv", "Zy", "Dm", "qita"):
            pages.append("/{}/index.html".format(c))
            for gp in range(2, 16):
                pages.append("/{}/index{}.html".format(c, gp))

        results = [None] * len(pages)

        def grab(i):
            try:
                results[i] = self._parse_cards(
                    self._get(pages[i], cache=300), limit=40)
            except Exception:
                results[i] = []

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(grab, range(len(pages))))
        except Exception:
            for i in range(len(pages)):
                grab(i)

        pool, seen = [], set()
        for cards in results:
            for it in (cards or []):
                if it["vod_id"] not in seen:
                    seen.add(it["vod_id"])
                    pool.append(it)

        hits = [x for x in pool if kw in x["vod_name"]]
        if not hits and re.match(r"^[A-Za-z0-9_ -]{2,}$", kw):
            k = kw.lower().replace(" ", "")
            hits = [x for x in pool if k in x["vod_id"].lower()]
        return hits

    def detailContent(self, ids):
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        paths = [str(i) for i in ids if str(i).strip()]
        if not paths:
            return {"list": []}
        path = paths[0]
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path += "/"
        html = self._get(path, cache=600)
        if not html or "404 Not Found" in html[:400]:
            return {"list": []}

        # 片名：h1《xxx》
        name = ""
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        if m:
            raw = self._clean(m.group(1))
            mm = re.search(r'《([^》]+)》', raw)
            name = self._clean(mm.group(1) if mm else raw)
            # 去英文副标题：《坠落2：死点 / Fall 2》→ 坠落2：死点
            if "/" in name:
                left = name.split("/")[0].strip()
                # 中文主体在斜杠前才拆
                if left and re.search(r'[\u4e00-\u9fff]', left):
                    name = left
        if not name:
            m = re.search(r'og:title"\s+content="([^"]+)"', html)
            name = self._clean(m.group(1)) if m else "未知"

        # 封面
        pic = ""
        pm = re.search(r'class="detail-poster"[^>]*>\s*<img[^>]+src="([^"]+)"', html) or \
             re.search(r'<img[^>]+class="img-fluid lazy"[^>]+src="([^"]+)"', html) or \
             re.search(r'og:image"\s+content="([^"]+)"', html)
        if pm:
            pic = pm.group(1)
            if pic.startswith("//"):
                pic = "https:" + pic

        def field(label):
            # 真实结构：<span class="detail-label">◎影片导演：</span><b>麦浩邦</b>
            m2 = re.search(r'◎影片' + label + r'[：:]?</span>\s*<b>(.{0,400}?)</b>', html, re.S)
            if not m2:
                m2 = re.search(label + r'[：:]?</span>\s*<b>(.{0,400}?)</b>', html, re.S)
            if not m2:
                m2 = re.search(label + r'[：:]?\s*</[^>]+>\s*<[^>]*>(.{0,400}?)</', html, re.S)
            if not m2:
                m2 = re.search(label + r'[：:]\s*(.{0,200}?)(?:<br|</p|</div)', html, re.S)
            if not m2:
                m2 = re.search(label + r'[：:]([^<]{1,180})', html)
            return self._clean(m2.group(1)) if m2 else ""

        director = field("导演")
        actor = field("主演") or field("演员")
        area = field("地区")
        lang = field("语言")
        genre = field("类型")
        if not genre:
            m2 = re.search(r'og:video:class"\s+content="([^"]+)"', html)
            genre = self._clean(m2.group(1)) if m2 else ""
        year = ""
        m2 = re.search(r'(20\d{2}|19\d{2})', field("年份"))
        if m2:
            year = m2.group(1)
        state = field("状态")

        # 简介：meta Description（★大写D，正则必须忽略大小写）→ 抠剧情简介正文
        content = ""
        m2 = re.search(r'name="description"\s+content="([^"]+)"', html, re.I)
        if m2:
            raw = self._clean(m2.group(1))
            # 形如：《片名》（）是2026年...剧情简介：正文。。高清全集...尽在Auete影视网。
            mm = re.search(r'剧情简介[：:](.*?)(?:。?高清|。。|尽在Auete)', raw, re.S)
            if mm:
                content = self._clean(mm.group(1)).rstrip("。.")
            else:
                content = re.sub(r'^《[^》]+》.*?剧情简介[：:]', "", raw)
                content = re.sub(r'。?高清全集免费在线观看.*$', "", content)
                content = re.sub(r'尽在Auete影视网。?$', "", content).strip()
        if not content:
            m2 = re.search(r'剧情简介[：:]\s*(.{0,600}?)(?:</|$)', html, re.S)
            if m2:
                content = self._clean(m2.group(1))

        # ---- 播放线路分块（id="player_list" 切段）----
        play_from, play_url = [], []
        marks = [m.start() for m in re.finditer(r'id="player_list"', html)]
        for i, st in enumerate(marks):
            end = marks[i + 1] if i + 1 < len(marks) else len(html)
            block = html[st:end]
            lm = re.search(r'『[^』]+』([^<]+)</b>', block)
            lname = self._clean(lm.group(1)) if lm else "线路{}".format(i + 1)
            eps = re.findall(
                r'href="([^"]*?play-(\d+)-(\d+)\.html)"[^>]*?(?:title="([^"]*)")?[^>]*>([^<]{0,20})</a>',
                block)
            if not eps:
                continue
            parts, seen_ep = [], set()
            for href, sid, nid, t1, t2 in eps:
                if nid in seen_ep:
                    continue
                seen_ep.add(nid)
                epname = self._clean(t1) or self._clean(t2) or "第{}集".format(int(nid) + 1)
                parts.append("{}${}".format(epname, href))
            if parts:
                def ekey(s):
                    m3 = re.search(r"play-\d+-(\d+)\.html", s.split("$")[-1])
                    return int(m3.group(1)) if m3 else 0
                parts.sort(key=ekey)
                play_from.append(lname or "线路")
                play_url.append("#".join(parts))

        # 兜底：线路块没切出来但页面有播放链接
        if not play_url:
            eps = re.findall(
                r'href="([^"]*?play-(\d+)-(\d+)\.html)"[^>]*>([^<]{0,20})</a>', html)
            if eps:
                parts, seen_ep = [], set()
                for href, sid, nid, t in eps:
                    if nid in seen_ep:
                        continue
                    seen_ep.add(nid)
                    epname = self._clean(t) or "第{}集".format(int(nid) + 1)
                    parts.append("{}${}".format(epname, href))
                if parts:
                    play_from.append("主线")
                    play_url.append("#".join(parts))

        vod = {
            "vod_id": path,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": state,
            "vod_year": year,
            "vod_area": area,
            "vod_lang": lang,
            "vod_genre": genre,
            "vod_director": director,
            "vod_actor": actor,
            "vod_content": content,
        }
        if play_url:
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_url)
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags):
        """id = 播放页相对路径，如 /Movie/xx/yy/play-0-0.html
        播放页 var now=base64decode("...") → m3u8 直链
        flag/vipFlags 真用上：线路名含'嗅探'强制 parse:1"""
        p = str(id or "").strip()
        if "@@" in p:
            p = p.split("@@")[-1]
        fl = str(flag or "")
        if "嗅探" in fl:
            return {"parse": 1, "url": (self.host + p) if p.startswith("/") else p,
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        if "play-" not in p:
            return {"parse": 0, "url": "", "header": {}}
        if not p.startswith("/"):
            p = "/" + p
        html = self._get(p, cache=0, timeout=15)
        url = ""
        if html:
            # 1) var now=base64decode("...")
            for m in re.finditer(
                    r'base64decode\(\s*["\']([A-Za-z0-9+/=]{16,})["\']\s*\)', html):
                try:
                    dec = base64.b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)) \
                        .decode("utf-8", "ignore")
                    dec = unquote(dec).strip()
                    if dec.startswith("http") and self.isVideoFormat(dec):
                        url = dec
                        break
                    if dec.startswith("http") and not url:
                        url = dec
                except Exception:
                    continue
            # 2) 多正则兜底
            if not self.isVideoFormat(url):
                for pat in (r'var\s+now\s*=\s*["\'](https?://[^"\']+)["\']',
                            r'file\s*:\s*["\']([^"\']+)["\']',
                            r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)',
                            r'(https?://[^"\'\s]+\.mp4[^"\'\s]*)'):
                    m2 = re.search(pat, html)
                    if m2:
                        url = m2.group(1)
                        break

        if url and self.isVideoFormat(url):
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.UA,
                               "Referer": self.host + "/"}}
        if url:
            return {"parse": 1, "url": url, "header": {"User-Agent": self.UA}}
        return {"parse": 1, "url": self.host + p,
                "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}

    def localProxy(self, param=None):
        # 四元组契约：不代理（封面/流公网直连）
        return [404, "text/plain", b"not supported", {}]
