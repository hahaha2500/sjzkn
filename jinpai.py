# -*- coding: utf-8 -*-
# =====================================================================
# m.vv3nwjk.com (金牌影院)  TVBox·FongMi type=3 Python 点播源
# 站型：Next.js App Router + WAF，无采集接口 → 全 API 直连（签名已逆向）
#
# 【逆向成果·签名算法 2026-09-28 实测验签 100% 通过】
#   JS 位置: app/(page)/layout chunk 模块 88773 requestUseSign + 模块 22331 sign()
#   signKey = "cb808529bae6b6be45ecfab29a4889bc"
#   GET:  A = "按key排序的k=v&k=v" + "&key={signKey}&t={毫秒时间戳}"
#         sign = SHA1( MD5(A).hex ).hex
#   headers: sign / t / deviceId(uuid) / authorization:"" / client-type:3
#   deviceId 来源 localStorage _uuid_ (任意uuid实测可用)
#
# 【API 契约（全部实测 200）】
#   分类/首页  GET /api/mw-movie/anonymous/video/list?pageNum&pageSize&sort=1&sortBy=1&type1={tid}
#              → data.{totalCount,totalPage,list:[{vodId,vodName,vodPic,vodClass,
#                vodRemarks,vodScore,vodDoubanScore,vodActor,vodYear,...}]}
#   搜索       GET /api/mw-movie/anonymous/video/searchByWord?keyword&pageNum&pageSize&sourceCode=1
#              → data.result.{totalCount,totalPage,list:[同上+vodBlurb/vodDirector/vodLang]}
#   取流       GET /api/mw-movie/anonymous/v2/video/episode/url?clientType=3&id={vid}&nid={nid}
#              → data.list:[{resolutionName:蓝光/高清/标清, url:...m3u8?...}]   需签名
#              m3u8 直取 200 (头: UA+Referer)
#   详情       GET /detail/{id} 静态SSR → 内嵌 \"params\":{\"id\":\"X\"} 之后的
#              playListData 块: vodName/vodPic/vodActor/vodDirector/vodClass/
#              vodRemarks/vodYear/vodArea/vodLang/vodScore + episodeList[{nid,name,sort}]
#   分类tid    1电影 2电视剧 3综艺 4动漫 88短剧 (首页导航实测)
#   集锚点     /vod/play/{vid}/{sid}/{nid}  (detail DOM 兜底用)
#
# 设计：详情4线路 = 蓝光/高清/标清(API按清晰度取流) + 原页嗅探(parse0回播放页)
#       pid = q{N}@@{vid}@@{nid} / sniff@@/vod/play/...
# 加载层铁律(默影视实测模板)：getDependence→[] / 显式__init__调基类 /
#       BaseSpider别名 / localProxy(param=None)
# =====================================================================
import re
import json
import time
import hashlib

try:
    from urllib.parse import quote, urlencode
except Exception:
    from urllib import quote, urlencode

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

    NAME = "金牌影院"
    HOST = "https://m.vv3nwjk.com"
    UA = ("Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

    SIGN_KEY = "cb808529bae6b6be45ecfab29a4889bc"
    DEVICE_ID = "41e8779b-344b-4159-8ad4-8cee6f726543"   # localStorage _uuid_ 模板

    CLASSES = [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "电视剧"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
        {"type_id": "88", "type_name": "短剧"},
    ]
    QUALITIES = ["蓝光", "高清", "标清"]

    # ---------------- 加载层（默影视铁律） ----------------
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
        # 默影视铁律：[] 不给宿主加依赖负担
        return []

    def isVideoFormat(self, url):
        u = str(url or "").lower().split("?")[0]
        return any(u.endswith(e) for e in
                   [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    # ---------------- 签名（逆向成果） ----------------
    def _sign_headers(self, params, referer=None):
        """GET 参数签名: sign=SHA1(MD5(sorted_kvs + &key=..&t=..))"""
        t = str(int(time.time() * 1000))
        kv = "&".join("{}={}".format(k, params[k]) for k in sorted(params))
        raw = "{}&key={}&t={}".format(kv, self.SIGN_KEY, t)
        sig = hashlib.sha1(hashlib.md5(raw.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
        h = {
            "sign": sig,
            "t": t,
            "deviceId": self.DEVICE_ID,
            "authorization": "",
            "client-type": "3",
            "Accept": "application/json, text/plain, */*",
        }
        if referer:
            h["Referer"] = referer
        return h

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
                "Accept-Language": "zh-CN,zh;q=0.9",
            })

    def _get(self, path, params=None, timeout=15, cache=0, abs_url=None, headers=None):
        self._ensure()
        url = abs_url or (self.host + path)
        key = url + ("|" + urlencode(params) if params else "")
        if cache:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < cache:
                return hit[1]
        text = ""
        try:
            h = headers or {}
            if _HAS_RQ:
                r = self.session.get(url, params=params, headers=h, timeout=timeout)
                if r.status_code >= 400:
                    self._last_err = "HTTP {}".format(r.status_code)
                    return ""
                text = r.text or ""
            else:
                q = "?" + urlencode(params) if params else ""
                req = urllib_request.Request(url + q, headers=dict(
                    {"User-Agent": self.UA, "Referer": self.host + "/"}, **h))
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

    def _api(self, path, params, referer=None, cache=0, timeout=15):
        """签名 API GET → dict"""
        h = self._sign_headers(params, referer=referer)
        raw = self._get(path, params=params, headers=h, cache=cache, timeout=timeout)
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

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
    def _score_str(it):
        for k in ("vodDoubanScore", "vodScore"):
            v = it.get(k)
            try:
                f = float(v)
                if f > 0:
                    return "{}分".format(("%g" % f))
            except Exception:
                continue
        return ""

    def _item_vod(self, it):
        vid = it.get("vodId")
        name = self._clean(it.get("vodName") or "")
        if not vid or not name:
            return None
        remarks = self._clean(it.get("vodRemarks") or "")
        sc = self._score_str(it)
        if sc:
            remarks = (remarks + " " + sc).strip()
        vod = {
            "vod_id": str(vid),
            "vod_name": name,
            "vod_pic": it.get("vodPic") or "",
            "vod_remarks": remarks,
            "vod_year": self._clean(it.get("vodYear") or ""),
            "vod_area": self._clean(it.get("vodArea") or ""),
            "vod_lang": self._clean(it.get("vodLang") or ""),
            "vod_genre": self._clean(it.get("vodClass") or ""),
            "vod_actor": self._clean(it.get("vodActor") or ""),
            "vod_director": self._clean(it.get("vodDirector") or ""),
            "vod_content": self._clean(it.get("vodBlurb") or ""),
        }
        return vod

    # ---------------- 列表 API ----------------
    def _list_api(self, type_id, page=1, page_size=30, cache=180):
        """分类/首页列表。返回 (vods, pagecount, total)"""
        params = {"pageNum": page, "pageSize": page_size,
                  "sort": 1, "sortBy": 1, "type1": type_id}
        d = self._api("/api/mw-movie/anonymous/video/list", params,
                      referer=self.host + "/vod/show/id/{}".format(type_id),
                      cache=cache)
        data = d.get("data") or {}
        lst = data.get("list") or []
        vods = [v for v in (self._item_vod(it) for it in lst) if v]
        try:
            pagecount = int(data.get("totalPage") or 1) or 1
        except Exception:
            pagecount = 1
        try:
            total = int(data.get("totalCount") or 0) or 0
        except Exception:
            total = 0
        return vods, pagecount, total

    def _search_api(self, keyword, page=1, page_size=30, cache=180):
        params = {"keyword": keyword, "pageNum": page,
                  "pageSize": page_size, "sourceCode": 1}
        d = self._api("/api/mw-movie/anonymous/video/searchByWord", params,
                      referer=self.host + "/", cache=cache)
        res = ((d.get("data") or {}).get("result")) or {}
        lst = res.get("list") or []
        vods = [v for v in (self._item_vod(it) for it in lst) if v]
        try:
            pagecount = int(res.get("totalPage") or 1) or 1
        except Exception:
            pagecount = 1
        try:
            total = int(res.get("totalCount") or 0) or 0
        except Exception:
            total = 0
        return vods, pagecount, total

    # ---------------- 五接口 ----------------
    def homeContent(self, filter=False):
        try:
            classes = [{"type_id": c["type_id"], "type_name": c["type_name"]}
                       for c in self.CLASSES]
            return {"class": classes, "list": self.homeVideoContent().get("list", [])}
        except Exception as e:
            return {"class": [{"type_id": c["type_id"], "type_name": c["type_name"]}
                              for c in self.CLASSES],
                    "list": [self._diag_card("首页异常",
                                             "{}: {}".format(type(e).__name__, str(e)[:80]))]}

    def homeVideoContent(self):
        # 首页 = 5分类各取首批, 交错合并 (API自带海报, 无需回填)
        try:
            results = [None] * len(self.CLASSES)

            def grab(i):
                try:
                    results[i] = self._list_api(self.CLASSES[i]["type_id"],
                                                1, 12, cache=300)[0]
                except Exception:
                    results[i] = []

            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=5) as ex:
                    list(ex.map(grab, range(len(self.CLASSES))))
            except Exception:
                for i in range(len(self.CLASSES)):
                    grab(i)

            vods, seen = [], set()
            for slot in range(12):        # 交错: 每分类逐条轮转
                for blk in results:
                    if blk and slot < len(blk):
                        v = blk[slot]
                        if v["vod_id"] not in seen:
                            seen.add(v["vod_id"])
                            vods.append(v)
                    if len(vods) >= 36:
                        break
                if len(vods) >= 36:
                    break
            if not vods:
                return {"list": [self._diag_card("首页抓取失败",
                         getattr(self, "_last_err", "") or "API返回0条")]}
            return {"list": vods}
        except Exception as e:
            return {"list": [self._diag_card("首页异常",
                     "{}: {}".format(type(e).__name__, str(e)[:80]))]}

    def _diag_card(self, title, remark):
        return {"vod_id": "0", "vod_name": "[诊断] " + str(title)[:40],
                "vod_pic": "", "vod_remarks": str(remark)[:60]}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        tid = str(tid or "1").strip()
        # 兼容 tid 传 URL / 中文名
        if not tid.isdigit():
            m = re.search(r"/vod/show/id/(\d+)", tid)
            if m:
                tid = m.group(1)
            else:
                for c in self.CLASSES:
                    if tid == c["type_name"]:
                        tid = c["type_id"]
                        break
                else:
                    tid = "1"
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        vods, pagecount, total = self._list_api(tid, pg, page_size=30, cache=180)
        if not vods and pg > 1:
            return {"page": pg, "pagecount": max(pg - 1, 1), "limit": 30,
                    "total": total, "list": []}
        return {"page": pg, "pagecount": pagecount, "limit": 30,
                "total": total, "list": vods}

    def searchContent(self, key, quick, pg="1"):
        kw = str(key or "").strip()
        if not kw:
            return {"page": 1, "pagecount": 1, "limit": 30, "total": 0, "list": []}
        try:
            pg = int(pg) or 1
        except Exception:
            pg = 1
        # quick 真用上: 快搜跳过缓存
        vods, pagecount, total = self._search_api(kw, pg, page_size=30,
                                                  cache=0 if quick else 180)
        return {"page": pg, "pagecount": pagecount, "limit": 30,
                "total": total, "list": vods}

    # ---------------- 详情：SSR 静态 + playListData 内嵌块 ----------------
    @staticmethod
    def _esc_field(seg, key):
        """在转义JSON片段里取 \"key\":\"value\"（首个命中=当前片）"""
        m = re.search(r'\\"' + key + r'\\":\\"((?:[^"\\]|\\.){0,900}?)\\"', seg)
        if m:
            return m.group(1).replace('\\/', '/').replace('\\"', '"')
        m = re.search(r'"' + key + r'":"((?:[^"\\]|\\.){0,900}?)"', seg)
        if m:
            return m.group(1).replace('\\/', '/')
        return ""

    def detailContent(self, ids):
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        vids = [str(i).strip() for i in ids if str(i).strip()]
        if not vids:
            return {"list": []}
        vid = vids[0]
        if not vid.isdigit():            # 兼容传完整路径
            m = re.search(r"(\d{3,})", vid)
            if not m:
                return {"list": []}
            vid = m.group(1)
        html = self._get("/detail/" + vid, cache=600, timeout=15)
        if not html:
            return {"list": []}

        # 定位当前片的数据块: \"params\":{\"id\":\"VID\"} 之后是它的 playListData
        anchor = '\\"params\\":{\\"id\\":\\"' + vid + '\\"'
        i = html.find(anchor)
        seg = html[i:i + 80000] if i >= 0 else html

        name = self._esc_field(seg, "vodName")
        pic = self._esc_field(seg, "vodPic")
        actor = self._esc_field(seg, "vodActor")
        director = self._esc_field(seg, "vodDirector")
        genre = self._esc_field(seg, "vodClass")
        remarks = self._esc_field(seg, "vodRemarks")
        year = self._esc_field(seg, "vodYear")
        area = self._esc_field(seg, "vodArea")
        lang = self._esc_field(seg, "vodLang")
        score = self._esc_field(seg, "vodScore") or self._esc_field(seg, "vodDoubanScore")

        # 简介: meta description 兜底
        content = ""
        m = re.search(r'name="description"\s+content="([^"]{20,600})"', html)
        if m:
            content = self._clean(m.group(1))
            content = re.sub(r"^《[^》]+》是由.*?等主演的\S{1,6}。金牌影院为您提供[^。]*。",
                             "", content)
            content = re.sub(r"^《[^》]+》简介：", "", content)

        # 集数组: episodeList\":[{...\"nid\":N,\"name\":\"..\"...}] （括号平衡）
        episodes = []
        j = seg.find('"episodeList\\":[')
        if j < 0:
            j = seg.find('"episodeList":[')
        if j >= 0:
            k = seg.find("[", j)
            depth, end = 0, -1
            for p in range(k, min(k + 60000, len(seg))):
                c = seg[p]
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        end = p
                        break
            if end > k:
                block = seg[k:end + 1]
                eps = re.findall(r'\\"nid\\":(\d+),\\"name\\":\\"((?:[^"\\]|\\.){0,60}?)\\"', block)
                if not eps:
                    eps = re.findall(r'"nid":(\d+),"name":"((?:[^"\\]|\\.){0,60}?)"', block)
                for nid, nm in eps:
                    episodes.append((nid, nm.replace('\\"', '"')))

        # DOM 兜底: 播放锚点 (同时拿 sid 供嗅探线用)
        sid = "1"
        m = re.search(r'href="/vod/play/' + vid + r'/(\d+)/(\d+)"', html)
        if m:
            sid = m.group(1)
        if not episodes:
            dom = re.findall(r'href="/vod/play/' + vid + r'/(\d+)/(\d+)"[^>]*>'
                             r'(?:<div[^>]*>)?([^<]{0,20})', html)
            seen = set()
            for s_, nid, nm in dom:
                sid = s_
                if nid in seen:
                    continue
                seen.add(nid)
                episodes.append((nid, self._clean(nm) or "第{}集".format(len(seen))))
        if not name:
            m = re.search(r'<title>([^_|<]{1,60})', html)
            name = self._clean(m.group(1)) if m else "未知"

        # ---- 线路: 蓝光/高清/标清(API按清晰度取流) + 原页嗅探 ----
        play_from, play_url = [], []
        if episodes:
            for qi, qn in enumerate(self.QUALITIES):
                parts = []
                for nid, nm in episodes:
                    nm = self._clean(nm) or "第{}集".format(len(parts) + 1)
                    parts.append("{}${}".format(nm, "q{}@@{}@@{}".format(qi, vid, nid)))
                play_from.append("金牌·" + qn)
                play_url.append("#".join(parts))
            sniff = []
            for nid, nm in episodes:
                nm = self._clean(nm) or "第{}集".format(len(sniff) + 1)
                sniff.append("{}${}".format(
                    nm, "sniff@@/vod/play/{}/{}/{}".format(vid, sid, nid)))
            play_from.append("金牌·原页嗅探")
            play_url.append("#".join(sniff))

        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": (remarks + " " + (score + "分" if score and score not in ("0", "0.0", "无") else "")).strip(),
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

    # ---------------- 播放：签名取流 ----------------
    def playerContent(self, flag, id, vipFlags):
        pid = str(id or "").strip()
        fl = str(flag or "")
        # 嗅探线 / flag 含「嗅探」 → 回播放页交 App 内建嗅探
        if pid.startswith("sniff@@") or "嗅探" in fl:
            path = pid.split("@@", 1)[-1] if "@@" in pid else pid
            if not path.startswith("/"):
                path = "/" + path
            return {"parse": 0, "url": self.host + path,
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        # 直连: q{N}@@{vid}@@{nid}
        m = re.match(r"^q(\d+)@@(\d+)@@(\d+)$", pid)
        if not m:
            # 兼容裸 nid 无从解析 → 空不崩
            if pid.startswith("http") and self.isVideoFormat(pid):
                return {"parse": 0, "url": pid,
                        "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
            return {"parse": 0, "url": "", "header": {}}
        qi, vid, nid = int(m.group(1)), m.group(2), m.group(3)

        # flag 真用上: 线路名带清晰度则覆盖
        for idx, qn in enumerate(self.QUALITIES):
            if qn in fl:
                qi = idx
                break

        params = {"clientType": "3", "id": vid, "nid": nid}
        h = self._sign_headers(params,
                               referer=self.host + "/vod/play/{}/{}/{}".format(vid, "1", nid))
        raw = self._get("/api/mw-movie/anonymous/v2/video/episode/url",
                        params=params, headers=h, cache=0, timeout=15)
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        lst = ((d.get("data") or {}).get("list")) or []
        if not lst:
            # 取不到 → 回播放页嗅探兜底
            return {"parse": 0,
                    "url": self.host + "/vod/play/{}/1/{}".format(vid, nid),
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        # 按清晰度选择 (接口顺序 蓝光→高清→标清, 兼容按 resolution 排序)
        if qi < len(lst):
            chosen = lst[qi]
        else:
            chosen = lst[-1]
        url = str(chosen.get("url") or "")
        if not url:
            url = str(lst[0].get("url") or "")
        if url:
            return {"parse": 0, "url": url,
                    "header": {"User-Agent": self.UA, "Referer": self.host + "/"}}
        return {"parse": 1, "url": self.host + "/vod/play/{}/1/{}".format(vid, nid),
                "header": {"User-Agent": self.UA}}

    def localProxy(self, param=None):
        # 四元组契约：不代理（封面/流公网直连）
        return [404, "text/plain", b"not supported", {}]
