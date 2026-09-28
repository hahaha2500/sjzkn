# -*- coding: utf-8 -*-
# =====================================================================
# zip0.com (ZIP0 影视搜索聚合站)  TVBox·FongMi type=3 Python 点播源
# 站点：Cloudflare + TanStack SSR 搜索聚合站 —— 无采集接口，但有官方公开 API
# URL/API 规律（实测 2026-09-28）：
#   ★官方API  /.well-known/openapi.json → "ZIP0 Public Media Discovery API v1.1.0"
#   搜索      GET /api/videos/search?query=&page=&limit=(max50)
#             → {success,data:[{title,year,category,area,language,score,remarks,
#                episodeCount,updatedAt,url}],pagination:{total,page,limit,pages},
#                sources:{completed,total}}   （10个上游源聚合；仅标题匹配，无分类过滤）
#   观看页    /watch?source={src}&id={id}&episode={n}  SSR直出：
#             og:image=海报、RSC字段 title/year/remarks/category/score/actors/director
#             + episodes:$R[..]=[{name:"第1集",url:"https://…/index.m3u8"},…] 全集流
#   m3u8头    需 UA + Referer: https://zip0.com/（裸请求403）
#   直播/广播 /api/tv/channels · /api/radio/stations（本源专注点播）
# 设计：分类=关键词聚合（站无分类维度）；列表海报=并行抓观看页 og:image
#       详情多线路=同名片聚合上游源（电影天堂/如意/非凡…）$$$ 分组
#       播放双线路：直连(parse0+UA/Referer直出m3u8) / 嗅探(parse0回观看页)
# 加载层铁律（默影视实测能出片模板 jook/77dpw4）：
#   getDependence()→[] / 显式 __init__ 先调基类 / BaseSpider 别名 / localProxy 默认参
# =====================================================================
import re
import json
import time

try:
    from urllib.parse import quote, urljoin, urlencode
except Exception:
    from urllib import quote, urlencode
    urljoin = lambda a, b: a.rstrip('/') + '/' + b.lstrip('/')

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self, *a, **k):
            pass

try:
    import requests as rq
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry as _Retry
    except Exception:
        _Retry = None
    _HAS_RQ = True
except Exception:
    _HAS_RQ = False
    import urllib.request as urllib_request
    import urllib.error as urllib_error


class Spider(BaseSpider):

    NAME = "ZIP0"
    HOST = "https://zip0.com"
    UA = ("Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

    # 分类 = 关键词聚合（该站是搜索引擎型站点，无真实分类；词均为标题高频词，实测命中量足）
    CLASSES = [
        {"type_id": "最新", "type_name": "最新热播"},
        {"type_id": "电影", "type_name": "电影"},
        {"type_id": "电视剧", "type_name": "电视剧"},
        {"type_id": "动漫", "type_name": "动漫"},
        {"type_id": "综艺", "type_name": "综艺"},
        {"type_id": "恐怖", "type_name": "恐怖"},
        {"type_id": "喜剧", "type_name": "喜剧"},
        {"type_id": "爱情", "type_name": "爱情"},
        {"type_id": "动作", "type_name": "动作"},
        {"type_id": "悬疑", "type_name": "悬疑"},
        {"type_id": "纪录片", "type_name": "纪录片"},
        {"type_id": "武侠", "type_name": "武侠"},
    ]
    # 首页「大家在搜」热词（取自站方首页）
    HOT = ["星际穿越", "漫长的季节", "琅琊榜", "流浪地球"]
    # 上游源 key → 中文名（详情多线路用，未收录的用原 key）
    SRC_NAME = {
        "dyttzy": "电影天堂", "ruyi": "如意", "bfzy": "非凡影视",
        "imgo": "芒果TV", "qiyi": "爱奇艺", "youku": "优酷",
        "qq": "腾讯视频", "bilibili": "B站", "mgtv": "芒果TV",
    }

    # ---------------- 加载层（默影视铁律，一行不能少） ----------------
    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = self.HOST
        self.session = None
        self._cache = {}
        self._last_err = ""

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
        if not hasattr(self, "_last_err"):
            self._last_err = ""
        if _HAS_RQ and getattr(self, "session", None) is None:
            self.session = rq.Session()
            try:
                if _Retry is not None:
                    retry = _Retry(total=3, backoff_factor=0.5,
                                   status_forcelist=[429, 500, 502, 503, 504],
                                   allowed_methods=["GET"])
                    ad = HTTPAdapter(pool_connections=4, pool_maxsize=8,
                                     max_retries=retry)
                else:
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

    def _get(self, path, params=None, timeout=15, cache=0, abs_url=None):
        self._ensure()
        url = abs_url or (self.host + path)
        key = url + ("|" + urlencode(params) if params else "")
        if cache:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < cache:
                return hit[1]
        text = ""
        try:
            if _HAS_RQ:
                r = self.session.get(url, params=params, timeout=timeout)
                if r.status_code >= 400:
                    self._last_err = "HTTP {}".format(r.status_code)
                    return ""
                text = r.text or ""
            else:
                q = "?" + urlencode(params) if params else ""
                req = urllib_request.Request(url + q, headers={
                    "User-Agent": self.UA, "Referer": self.host + "/"})
                with urllib_request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
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
            self._cache[key] = (time.time(), text)
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

    @staticmethod
    def _watch_path(url):
        """绝对/相对观看URL → 站内相对路径"""
        u = str(url or "").strip()
        if not u:
            return ""
        i = u.find("/watch?")
        if i >= 0:
            return u[i:]
        if u.startswith("/watch"):
            return u
        return ""

    @staticmethod
    def _wp_parts(watch_path):
        """观看路径 → (source, id)"""
        m = re.search(r"[?&]source=([^&]+)", watch_path or "")
        src = m.group(1) if m else ""
        m = re.search(r"[?&]id=([^&]+)", watch_path or "")
        vid = m.group(1) if m else ""
        return src, vid

    # ---------------- 搜索 API（官方公开） ----------------
    def _search_api(self, query, page=1, limit=20, cache=180):
        raw = self._get("/api/videos/search",
                        params={"query": query, "page": page, "limit": limit},
                        cache=cache)
        try:
            d = json.loads(raw)
        except Exception:
            return [], {"total": 0, "page": page, "limit": limit, "pages": 1}
        if not isinstance(d, dict) or not d.get("success"):
            return [], {"total": 0, "page": page, "limit": limit, "pages": 1}
        items = d.get("data") or []
        pg = d.get("pagination") or {}
        meta = {
            "total": int(pg.get("total") or 0),
            "page": int(pg.get("page") or page or 1),
            "limit": int(pg.get("limit") or limit or 20),
            "pages": int(pg.get("pages") or 1) or 1,
        }
        return items, meta

    @staticmethod
    def _item_vod(it):
        """搜索条目 → vod（无海报，稍后回填）"""
        wp = Spider._watch_path(it.get("url") or "")
        name = Spider._clean(it.get("title") or "")
        if not name or not wp:
            return None
        remarks = Spider._clean(it.get("remarks") or "")
        score = Spider._clean(it.get("score") or "")
        if score and score not in ("0.0", "0"):
            remarks = (remarks + " " + score + "分").strip()
        vod = {
            "vod_id": wp,           # id 直接存观看路径 /watch?source=X&id=Y
            "vod_name": name,
            "vod_pic": "",
            "vod_remarks": remarks,
            "vod_year": Spider._clean(it.get("year") or ""),
            "vod_area": Spider._clean(it.get("area") or ""),
            "vod_lang": Spider._clean(it.get("language") or ""),
            "vod_genre": Spider._clean(it.get("category") or ""),
        }
        return vod

    # ---------------- 海报回填（并行抓观看页 og:image） ----------------
    def _fill_posters(self, vods, workers=8, timeout=8):
        """列表无海报字段 → 从各观看页抠 og:image。失败留空不崩。"""
        todo = [v for v in vods if not v.get("vod_pic") and v.get("vod_id", "").startswith("/watch")]
        if not todo:
            return vods
        uniq = {}
        for v in todo:
            uniq.setdefault(v["vod_id"], v)
        # 观看页结果缓存(含海报+全集流), detail 复用
        def grab(wp):
            try:
                html = self._get(wp, cache=600, timeout=timeout)
                pic = ""
                m = re.search(r'og:image"\s+content="([^"]+)"', html or "")
                if m:
                    pic = m.group(1)
                    if pic.startswith("//"):
                        pic = "https:" + pic
                return wp, pic
            except Exception:
                return wp, ""
        pairs = []
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:
                pairs = list(ex.map(grab, [v["vod_id"] for v in todo]))
        except Exception:
            pairs = [grab(v["vod_id"]) for v in todo]
        pmap = dict(pairs)
        for v in vods:
            if not v.get("vod_pic"):
                p = pmap.get(v.get("vod_id"))
                if p:
                    v["vod_pic"] = p
        return vods

    # ---------------- 五接口 ----------------
    def homeContent(self, filter=False):
        try:
            classes = [{"type_id": c["type_id"], "type_name": c["type_name"]}
                       for c in self.CLASSES]
            return {"class": classes, "list": self.homeVideoContent().get("list", [])}
        except Exception as e:
            return {"class": [{"type_id": c["type_id"], "type_name": c["type_name"]}
                              for c in self.CLASSES],
                    "list": [{"vod_id": "/", "vod_name": "[诊断] 首页异常",
                              "vod_pic": "",
                              "vod_remarks": "{}: {}".format(type(e).__name__, str(e)[:60])}]}

    def homeVideoContent(self):
        # 首页 = 热词并行搜索聚合 + 海报回填
        try:
            results = [None] * len(self.HOT)

            def grab(i):
                try:
                    results[i] = self._search_api(self.HOT[i], 1, 8, cache=300)[0]
                except Exception:
                    results[i] = []

            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=4) as ex:
                    list(ex.map(grab, range(len(self.HOT))))
            except Exception:
                for i in range(len(self.HOT)):
                    grab(i)

            vods, seen = [], set()
            for block in results:
                for it in (block or []):
                    v = self._item_vod(it)
                    if v and v["vod_id"] not in seen:
                        seen.add(v["vod_id"])
                        vods.append(v)
            vods = vods[:36]
            self._fill_posters(vods)
            if not vods:
                return {"list": self._diag_home()}
            return {"list": vods}
        except Exception as e:
            return {"list": [self._diag_card("首页异常",
                                             "{}: {}".format(type(e).__name__, str(e)[:80]))]}

    def _diag_card(self, title, remark):
        return {"vod_id": "/", "vod_name": "[诊断] " + str(title)[:40],
                "vod_pic": "", "vod_remarks": str(remark)[:60]}

    def _diag_home(self):
        err = getattr(self, "_last_err", "") or "搜索API返回0条"
        return [self._diag_card("首页抓取失败", err)]

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        # 分类 = 关键词。兼容：tid 传中文词 / 完整搜索URL / extend.query 覆盖
        kw = str(tid or "最新").strip()
        if isinstance(extend, dict) and extend.get("query"):
            kw = str(extend["query"])
        if "/search" in kw or "search?" in kw:
            m = re.search(r"[?&]q=([^&]+)", kw)
            if m:
                try:
                    from urllib.parse import unquote as _uq
                    kw = _uq(m.group(1))
                except Exception:
                    kw = m.group(1)
        if kw in ("最新", "hot", ""):
            kw = "2026"     # 「最新」用年份词聚合，接近站方近期更新
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        items, meta = self._search_api(kw, pg, limit=20, cache=180)
        vods = [v for v in (self._item_vod(it) for it in items) if v]
        # 去重（同片多上游源会重复）
        vods = self._dedup(vods)
        self._fill_posters(vods)
        pagecount = meta["pages"] if meta["pages"] > 0 else (pg + 1 if vods else pg)
        return {"page": pg, "pagecount": pagecount, "limit": meta["limit"],
                "total": meta["total"], "list": vods}

    @staticmethod
    def _dedup(vods):
        out, seen = [], set()
        for v in vods:
            key = (v.get("vod_name"), v.get("vod_year"))
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
        return out

    def searchContent(self, key, quick, pg="1"):
        kw = str(key or "").strip()
        if not kw:
            return {"page": 1, "pagecount": 1, "limit": 20, "total": 0, "list": []}
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        # quick 真用上：快搜跳过缓存拿最新
        items, meta = self._search_api(kw, pg, limit=20,
                                       cache=0 if quick else 180)
        vods = [v for v in (self._item_vod(it) for it in items) if v]
        vods = self._dedup(vods)
        self._fill_posters(vods)
        return {"page": pg, "pagecount": meta["pages"], "limit": meta["limit"],
                "total": meta["total"], "list": vods}

    # ---------------- 详情：观看页 SSR 直出（RSC 流） ----------------
    @staticmethod
    def _parse_watch(html):
        """观看页 → {name, pic, year, category, remarks, score, actors,
                     director, content, episodes:[(name,url)]}"""
        out = {"name": "", "pic": "", "year": "", "category": "",
               "remarks": "", "score": "", "actors": "", "director": "",
               "content": "", "episodes": []}
        if not html:
            return out
        # 标题：RSC title:"漫长的季节" 最准（<title>带"第N集在线播放"噪音）
        m = re.search(r'\btitle:"([^"]{1,80})"', html)
        if m:
            out["name"] = m.group(1)
        if not out["name"]:
            m = re.search(r'<title>([^<|｜]+)', html)
            if m:
                out["name"] = re.sub(r'\s*第\s*\d+\s*集.*$', '', m.group(1)).strip()
        # 海报
        m = re.search(r'og:image"\s+content="([^"]+)"', html)
        if m:
            out["pic"] = m.group(1)
            if out["pic"].startswith("//"):
                out["pic"] = "https:" + out["pic"]
        # RSC 字段
        for key in ("year", "category", "remarks", "score", "actors", "director"):
            m = re.search(r'\b' + key + r':"([^"]{0,600})"', html)
            if m:
                out[key] = m.group(1)
        # 简介：RSC description 长文本（在 actors 之前的散文段）→ og:description 兜底
        m = re.search(r'description[":\s]+["\']((?:[^"\'\\]|\\.){80,2000})["\']', html)
        if m:
            out["content"] = m.group(1).replace("\\n", " ").replace('\\"', '"')
        if not out["content"]:
            m = re.search(r'name="description"\s+content="([^"]+)"', html)
            if m:
                out["content"] = m.group(1)
        # 全集流：episodes:$R[..]=[{name:"第1集",url:"https://…m3u8"},…]
        eps = re.findall(r'\{name:"([^"]{1,40})",url:"(https?://[^"]+?)"\}', html)
        if not eps:   # 兜底变体：JSON 双引号
            eps = re.findall(r'\{"name":"([^"]{1,40})","url":"(https?://[^"]+?)"\}', html)
        if not eps:   # 极限兜底：页面里裸 m3u8
            eps = [("正片", u) for u in
                   re.findall(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', html)]
        seen, uniq = set(), []
        for name, url in eps:
            name = name.strip()
            if url in seen:
                continue
            seen.add(url)
            uniq.append((name, url))
        out["episodes"] = uniq
        return out

    def detailContent(self, ids):
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        paths = [str(i) for i in ids if str(i).strip()]
        if not paths:
            return {"list": []}
        wp = paths[0]
        if not wp.startswith("/watch"):
            p = self._watch_path(wp)
            wp = p if p else ("/watch" + ("" if wp.startswith("?") else "?") + wp)
        html = self._get(wp, cache=600, timeout=15)
        if not html or "404" == str(html[:3]):
            return {"list": []}
        w = self._parse_watch(html)
        if not w["name"] and not w["episodes"]:
            return {"list": []}

        vod = {
            "vod_id": wp,
            "vod_name": w["name"] or "未知",
            "vod_pic": w["pic"],
            "vod_remarks": w["remarks"],
            "vod_year": w["year"],
            "vod_area": "",
            "vod_lang": "",
            "vod_genre": w["category"],
            "vod_director": w["director"],
            "vod_actor": w["actors"],
            "vod_content": self._clean(w["content"])[:800],
        }

        # ---- 单源：主线(直连) + 嗅探(回观看页) 两条线路 ----
        play_from, play_url = [], []
        src, vid = self._wp_parts(wp)
        if w["episodes"]:
            direct = ["{}${}".format(self._clean(n) or "第{}集".format(i + 1),
                                     self._safe_url(u))
                      for i, (n, u) in enumerate(w["episodes"])]
            sniff = ["{}${}".format(self._clean(n) or "第{}集".format(i + 1),
                                    "sniff@@" + wp)
                     for i, (n, u) in enumerate(w["episodes"])]
            play_from = ["ZIP0·直连", "ZIP0·原页嗅探"]
            play_url = ["#".join(direct), "#".join(sniff)]

        # ---- 多线路：搜索同名片聚合其它上游源（详情页实测同名多源） ----
        extras = self._extra_sources(w["vod_name"] if "vod_name" in w else vod["vod_name"],
                                     wp, vod["vod_year"])
        for label, ewp, eps in extras:
            segs = ["{}${}".format(self._clean(n) or "第{}集".format(i + 1),
                                   self._safe_url(u))
                    for i, (n, u) in enumerate(eps)]
            if segs:
                play_from.append(label)
                play_url.append("#".join(segs))

        if play_url:
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_url)
        return {"list": [vod]}

    @staticmethod
    def _safe_url(u):
        """播放地址里绝不能有 $ #（分隔符铁律）"""
        u = str(u or "")
        return u.replace("$", "%24").replace("#", "%23")

    def _extra_sources(self, name, skip_wp, year, max_n=3):
        """同名片 → 其它上游源的观看页（有限次，失败不崩）"""
        out = []
        if not name:
            return out
        try:
            items, _meta = self._search_api(name, 1, 20, cache=300)
        except Exception:
            return out
        got = 0
        for it in items:
            if got >= max_n:
                break
            wp = self._watch_path(it.get("url") or "")
            if not wp or wp == skip_wp:
                continue
            if year and str(it.get("year") or "") and str(it.get("year")) != str(year):
                continue
            src, vid = self._wp_parts(wp)
            if not src:
                continue
            html = self._get(wp, cache=600, timeout=12)
            if not html:
                continue
            w = self._parse_watch(html)
            if not w["episodes"]:
                continue
            # 同源去重（上游 key 相同视为同线路）
            label = "ZIP0·" + self.SRC_NAME.get(src, src)
            if any(lbl.endswith(label.split("·")[-1]) for lbl, _, _ in out):
                continue
            out.append((label, wp, w["episodes"]))
            got += 1
        return out

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        pid = str(id or "").strip()
        fl = str(flag or "")
        # flag 真用上：线路名含「嗅探」→ 回观看页交 App 内建嗅探
        if pid.startswith("sniff@@") or "嗅探" in fl:
            wp = pid.split("@@", 1)[-1] if "@@" in pid else pid
            if not wp.startswith("/"):
                wp = "/" + wp
            return {"parse": 0, "url": self.host + wp,
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        if not pid.startswith("http"):
            return {"parse": 0, "url": "", "header": {}}
        # 直连流：实测需 UA + zip0 Referer（裸请求403）
        if self.isVideoFormat(pid):
            return {"parse": 0, "url": pid,
                    "header": {"User-Agent": self.UA,
                               "Referer": self.host + "/"}}
        # 非直链（外站播放器页等）→ 交 App 嗅探
        return {"parse": 1, "url": pid, "header": {"User-Agent": self.UA}}

    def localProxy(self, param=None):
        # 四元组契约：不代理（封面/流公网直连）
        return [404, "text/plain", b"not supported", {}]
