# -*- coding: utf-8 -*-
"""
剧踪影院 (www.juzong01.me) — TVBox / FongMi type=3 爬虫
================================================================
【站点说明书 · 逆向要点】
1) 站型 = 苹果CMS v10 + stui(树懒) 模板，纯 HTML 直抓：
   - 采集接口已关：/api.php/provide/vod/ 会 301 回首页 HTML
   - 分类   /vodtype/{tid}/
   - 列表   /vodshow/{tid}-{area}-{by}--------{pg}------{year}/   (固定 12 段，位次: 1=地区 2=排序 8=页码 11=年份)
   - 详情   /voddetail/{id}/
   - 播放   /vodplay/{vid}-{sid}-{nid}/
   - 搜索   /vodsearch/{关键词}----------{pg}---/
2) 【WAF 403 挑战】首次访问 /vodsearch/ 返回 403 + 下发 cookie，
   同一会话（同一 cookie jar）重试一次即 200 —— 已实测通过。
3) 【播放地址】详情页只有集数链接；真地址在播放页 player_data：
     var player_data={...,"url":"明文或密文","from":"juzongx"...}
   - 明文：https://xxx/index.m3u8（dytt / 暴风BF / 西瓜 线路直接给）
   - 密文：juzongx- / juzong1- / juzong2- / juzong3- / JD- 开头（独家线路）
4) 【★密文解密 · 两步纯 HTTP 已实测跑通】
   GET https://jzpic.ok1333.cn/?url={urlencode(密文)}&ep=1
     -> HTML 里 window.WGART_PARSE_TOKEN="时间戳.随机.签名"
   GET https://jzpic.ok1333.cn/wgart/api.php?action=try_json_api
       &video_url={密文}&parse_token={token}
     -> {"success":true,"data":{"url":"真实 mp4/m3u8 直链"}}
5) 【取流头】只带 UA，不要带 Referer：douyinvod 直链带 Referer 会 403；
   西瓜线路 302 跳 jimxtc 交给播放器跟随即可。
6) 线路结构（实测 9 条）：独家[爽看]/[热播X]/[热播①②③] 走密文解密，
   BF/XG/DY[海外] 走明文 m3u8，酷播[国内] 是 youku 外链（回原页交 App 嗅探）。
"""

import re
import json
import time
from urllib.parse import quote, urljoin, unquote

try:
    import requests
    from requests.adapters import HTTPAdapter
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self, html=""):
            self.back_mode = ""
            self.cookie = None
            self.fetch = None


UA = ("Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# 占位图黑名单（宁窄勿宽）
_BAD_PIC = ("loading", "blank", "placeholder", "1x1", "transparent", ".svg", "lazy.gif")

# 密文前缀
_CIPHER_PRE = ("juzongx-", "juzong1-", "juzong2-", "juzong3-", "JD-")
_MEDIA_EXT = (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpd", ".m4s", ".mp4?")

# 解密中枢（站点自带播放器的上游）
JZ_PIC = "https://jzpic.ok1333.cn"


class Spider(BaseSpider):

    # ---------------- 加载层铁律 ----------------
    def __init__(self):
        try:
            super(Spider, self).__init__()
        except Exception:
            pass
        self.host = "https://www.juzong01.me"
        self.ua = UA
        self.session = None
        self._cache = {}
        self._ts = {}

    def init(self, extend=""):
        # extend 支持: "https://新域名" 或 {"host":"https://新域名"}
        try:
            ext = extend
            if isinstance(ext, str) and ext.strip():
                try:
                    ext = json.loads(ext)
                except Exception:
                    pass
            if isinstance(ext, dict):
                h = (ext.get("host") or ext.get("url") or "").strip()
            else:
                h = str(ext).strip()
            if h:
                if not h.startswith("http"):
                    h = "https://" + h
                self.host = h.rstrip("/")
        except Exception:
            pass
        return self

    def getName(self):
        return "剧踪"

    def getDependence(self):
        # 不给宿主加任何依赖负担（requests 自己 try import 兜底）
        return []

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower().split("?")[0]
        return any(u.endswith(ext) for ext in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg", ".mov", ".webm"))

    def manualVideoCheck(self):
        return False

    # ---------------- 网络单一入口 ----------------
    def _sess(self):
        if self.session is not None:
            return self.session
        if _HAS_REQ:
            s = requests.Session()
            try:
                adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
                s.mount("http://", adapter)
                s.mount("https://", adapter)
            except Exception:
                pass
            self.session = s
        return self.session

    def _get(self, url, referer=None, timeout=12, retry=2):
        """统一 GET。带 cookie 会话（过 WAF 403 挑战：403 后同会话重试即 200）。"""
        headers = {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        s = self._sess()
        last = ""
        if s is not None:
            for i in range(retry + 1):
                try:
                    r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                    if r.status_code == 200:
                        try:
                            if r.encoding in (None, "ISO-8859-1"):
                                r.encoding = "utf-8"
                        except Exception:
                            pass
                        return r.text
                    last = "HTTP %s" % r.status_code
                    # 403 = WAF 挑战，重试一次让会话 cookie 生效
                    if r.status_code not in (403, 429, 503):
                        break
                except Exception as e:
                    last = str(e)[:80]
                time.sleep(0.4 * (i + 1))
            # 极限兜底：urllib 再试一次
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    return data.decode(enc)
                except Exception:
                    continue
            return data.decode("utf-8", "ignore")
        except Exception as e:
            if not last:
                last = str(e)[:80]
            return ""

    def _get_bytes(self, url, referer=None, timeout=15):
        headers = {"User-Agent": self.ua}
        if referer:
            headers["Referer"] = referer
        s = self._sess()
        if s is not None:
            try:
                r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                if r.status_code == 200:
                    return r.content
            except Exception:
                pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            return b""

    def _get_json(self, url, referer=None, timeout=15):
        s = self._sess()
        headers = {"User-Agent": self.ua, "Accept": "application/json,text/plain,*/*"}
        if referer:
            headers["Referer"] = referer
        if s is not None:
            try:
                r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                if r.status_code == 200 and r.text.strip():
                    return json.loads(r.text)
            except Exception:
                pass
        txt = self._get(url, referer=referer, timeout=timeout)
        try:
            return json.loads(txt)
        except Exception:
            return None

    # ---------------- 公共小工具 ----------------
    @staticmethod
    def _clean(t):
        if not t:
            return ""
        t = re.sub(r"<[^>]+>", "", str(t))
        t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
        t = re.sub(r"\s+", " ", t)
        return t.strip()

    @staticmethod
    def _bad_pic(u):
        if not u:
            return True
        lu = u.lower()
        return any(b in lu for b in _BAD_PIC)

    def _pick_pic(self, block):
        for attr in ("data-original", "data-src", "data-lazy-src", "data-poster", "src"):
            m = re.search(attr + r'\s*=\s*"([^"]+)"', block)
            if m and not self._bad_pic(m.group(1)):
                u = m.group(1).strip()
                if u.startswith("//"):
                    u = "https:" + u
                return u
        return ""

    @staticmethod
    def _page_fields(tid, area="", by="", page=1, year=""):
        parts = [str(tid), area or "", by or "", "", "", "", "", "",
                 str(page), "", "", year or ""]
        return "/vodshow/" + "-".join(parts) + "/"

    # ---------------- 首页 ----------------
    def homeContent(self, filter=False):
        classes = [
            {"type_id": "1", "type_name": "电影"},
            {"type_id": "2", "type_name": "剧集"},
            {"type_id": "3", "type_name": "综艺"},
            {"type_id": "4", "type_name": "动漫"},
            {"type_id": "6", "type_name": "动作片"},
            {"type_id": "7", "type_name": "喜剧片"},
            {"type_id": "8", "type_name": "爱情片"},
            {"type_id": "9", "type_name": "科幻片"},
            {"type_id": "10", "type_name": "恐怖片"},
            {"type_id": "11", "type_name": "剧情片"},
            {"type_id": "12", "type_name": "战争片"},
            {"type_id": "22", "type_name": "犯罪片"},
            {"type_id": "23", "type_name": "动画片"},
            {"type_id": "13", "type_name": "国产剧"},
            {"type_id": "14", "type_name": "港台剧"},
            {"type_id": "15", "type_name": "日韩剧"},
            {"type_id": "16", "type_name": "欧美剧"},
            {"type_id": "20", "type_name": "海外剧"},
        ]
        flt = {}
        if filter:
            flt = self._build_filters(classes)

        vod = []
        try:
            vod = self.homeVideoContent().get("list", [])
        except Exception:
            vod = []

        # 分类动态校正（首页导航抓到就覆盖写死的）
        try:
            html = self._get(self.host + "/")
            if html:
                dyn = []
                for m in re.finditer(r'href="/vodtype/([^/"]+)/?"[^>]*>([^<]{1,12})</a>', html):
                    tid, name = m.group(1), self._clean(m.group(2))
                    if name and not name.startswith("http") and tid not in [c["type_id"] for c in dyn]:
                        dyn.append({"type_id": tid, "type_name": name})
                if len(dyn) >= 4:
                    classes = dyn
                    if filter:
                        flt = self._build_filters(classes)
        except Exception:
            pass

        return {"class": classes, "filters": flt, "list": vod}

    def _build_filters(self, classes):
        areas = ["大陆", "香港", "台湾", "美国", "日本", "韩国", "英国", "法国", "印度", "其他"]
        years = [str(y) for y in range(2026, 1989, -1)]
        bys = [("time", "最新"), ("hit", "最热"), ("score", "评分")]
        flt = {}
        for c in classes:
            tid = str(c["type_id"])
            flt[tid] = [
                {"key": "by", "name": "排序",
                 "value": [{"n": n, "v": v} for v, n in bys]},
                {"key": "area", "name": "地区",
                 "value": [{"n": "全部", "v": ""}] + [{"n": a, "v": a} for a in areas]},
                {"key": "year", "name": "年份",
                 "value": [{"n": "全部", "v": ""}] + [{"n": y, "v": y} for y in years]},
            ]
        return flt

    def homeVideoContent(self):
        html = self._get(self.host + "/")
        return {"list": self._parse_cards(html)}

    # ---------------- 列表卡片解析 ----------------
    def _parse_cards(self, html, limit=36):
        out = []
        seen = set()
        if not html:
            return out
        for m in re.finditer(r"<a[^>]*stui-vodlist__thumb[^>]*>", html):
            tag = m.group(0)
            end = html.find("</a>", m.end())
            block = tag + (html[m.end():end + 4] if end > 0 else "")
            href = re.search(r'href="([^"]+)"', tag)
            if not href:
                continue
            u = href.group(1)
            ids = re.search(r"voddetail/(\d+)", u)
            if not ids:
                continue
            vid = ids.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            name = ""
            mt = re.search(r'title="([^"]+)"', tag)
            if mt:
                name = self._clean(mt.group(1))
            if not name:
                mh = re.search(r'<h4[^>]*>\s*<a[^>]*>([^<]+)</a>', html[m.end():m.end() + 900])
                if mh:
                    name = self._clean(mh.group(1))
            if not name:
                ma = re.search(r'<a[^>]*title="([^"]+)"', block)
                name = self._clean(ma.group(1)) if ma else ""
            if not name:
                continue
            pic = self._pick_pic(block)
            remark = ""
            mr = re.search(r'<span[^>]*pic-text[^>]*>([^<]+)</span>', block)
            if mr:
                remark = self._clean(mr.group(1))
            out.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark,
            })
            if len(out) >= limit:
                break
        return out

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter=False, extend=None):
        pg = int(pg) if str(pg).isdigit() else 1
        extend = extend or {}
        area = str(extend.get("area", "") or "")
        by = str(extend.get("by", "") or "")
        year = str(extend.get("year", "") or "")

        tid = self._norm_tid(tid)
        url = self.host + self._page_fields(tid, area=area, by=by, page=pg, year=year)
        html = self._get(url, referer=self.host + "/")
        if not html or "stui-vodlist__thumb" not in html:
            # 位次异常兜底：标准 8+3 位
            url = self.host + "/vodshow/%s--------%d---/" % (tid, pg)
            html = self._get(url, referer=self.host + "/")

        lst = self._parse_cards(html)
        pagecount = self._pagecount_category(html, pg, lst)
        return {
            "page": pg,
            "pagecount": pagecount,
            "limit": 36,
            "total": pagecount * 36,
            "list": lst,
        }

    def _norm_tid(self, tid):
        # 兼容 传完整URL / 中文 / 拼音 的情况
        s = str(tid)
        m = re.search(r"vodtype/([^/]+)", s) or re.search(r"vodshow/(\d+)", s)
        if m:
            s = m.group(1)
        return s.strip() or "1"

    def _pagecount_category(self, html, pg, lst):
        if not lst:
            return pg
        # 尾页链接
        m = re.search(r'href="/vodshow/[^"]*?(\d+)---/"[^>]*>\s*尾页', html)
        if not m:
            m = re.search(r'href="/vodshow/(\d+)--------\d+---/"[^>]*>\s*尾页', html)
        if m:
            try:
                n = int(m.group(1))
                if n >= pg:
                    return n
            except Exception:
                pass
        m = re.search(r'<span class="num">\s*(\d+)\s*/\s*(\d+)\s*</span>', html)
        if m:
            try:
                n = int(m.group(2))
                if n >= pg:
                    return n
            except Exception:
                pass
        # 空页刹车：本页有货就还能往下翻一页
        return pg + 1 if len(lst) >= 5 else pg

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        pg = int(pg) if str(pg).isdigit() else 1
        kw = quote(str(key).strip())
        url = self.host + "/vodsearch/%s----------%d---/" % (kw, pg)
        html = self._get(url, referer=self.host + "/")
        if not html or "stui-vodlist__thumb" not in html:
            # 备用写法
            html = self._get(self.host + "/vodsearch/%s----------%d---.html" % (kw, pg),
                             referer=self.host + "/")
        lst = self._parse_cards(html, limit=30)
        pagecount = (pg + 1) if len(lst) >= 5 else pg
        return {
            "page": pg,
            "pagecount": pagecount,
            "limit": 30,
            "total": pagecount * 30,
            "list": lst,
        }

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        vod = {}
        try:
            vid = str(ids[0])
            m = re.search(r"(\d+)", vid)
            vid = m.group(1) if m else vid
            url = self.host + "/voddetail/%s/" % vid
            html = self._get(url, referer=self.host + "/")
            vod = self._parse_detail(html, vid, url)
        except Exception:
            vod = {}
        if not vod:
            return {"list": []}
        return {"list": [vod]}

    def _parse_detail(self, html, vid, url):
        if not html:
            return {}
        name = ""
        m = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h1>', html)
        if m:
            name = self._clean(m.group(1))
        if not name:
            m = re.search(r"<title>([^<-]+)", html)
            name = self._clean(m.group(1)) if m else ""
        if not name:
            return {}

        # 封面：h1 之前的最后一张图
        pic = ""
        mh = re.search(r'<h1[^>]*class="[^"]*title[^"]*"', html)
        if mh:
            head = html[max(0, mh.start() - 1500):mh.start()]
            cands = re.findall(r'(?:data-original|data-src|data-lazy-src|src)\s*=\s*"([^"]+)"', head)
            for c in reversed(cands):
                if not self._bad_pic(c):
                    pic = ("https:" + c) if c.startswith("//") else c
                    break

        # 简介
        desc = ""
        md = re.search(r'<span[^>]*class="[^"]*detail-content[^"]*"[^>]*>(.*?)</span>', html, re.S)
        if not md:
            md = re.search(r'<span[^>]*class="[^"]*detail-sketch[^"]*"[^>]*>(.*?)</span>', html, re.S)
        if md:
            desc = self._clean(md.group(1))
        if not desc:
            md = re.search(r"简介：</span>(.*?)</p>", html, re.S)
            if md:
                desc = self._clean(md.group(1))

        area = year = director = actor = genre = remark = ""
        for pm in re.finditer(r'<p class="data[^"]*">(.*?)</p>', html, re.S):
            blk = pm.group(1)
            txt = self._clean(blk)
            if "地区" in txt:
                area = txt.split("地区：", 1)[-1].strip()
            if "年份" in txt:
                year = txt.split("年份：", 1)[-1].strip()[:4]
            if "导演" in txt and not director:
                director = txt.split("导演：", 1)[-1].strip()
            if "主演" in txt and not actor:
                actor = txt.split("主演：", 1)[-1].strip()
            if "类型" in txt and not genre:
                genre = txt.split("类型：", 1)[-1].split("地区")[0].strip()
            if "更新" in txt and not remark:
                remark = txt.split("更新：", 1)[-1].strip()

        # 线路 + 集数
        froms, plays = self._parse_play_blocks(html, vid)

        # 无线路时的兜底：从立即播放按钮反推 1-1
        if not plays:
            mb = re.search(r'href="(/vodplay/(\d+)-(\d+)-(\d+)/)"', html)
            if mb:
                froms = ["剧踪·主线"]
                plays = ["%s$%s" % (name, mb.group(1))]

        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": remark,
            "vod_year": year,
            "vod_area": area,
            "vod_director": director,
            "vod_actor": actor,
            "vod_genre": genre,
            "vod_content": desc,
            "vod_play_from": "$$$".join(froms),
            "vod_play_url": "$$$".join(plays),
        }
        return vod

    def _parse_play_blocks(self, html, vid):
        """按 stui 模板线路块切分，返回 (线路名列表, 每线路 '集名$地址#集名$地址')"""
        heads = [m.start() for m in re.finditer(r'<div class="stui-pannel stui-pannel-bg clearfix">', html)]
        if not heads:
            heads = [m.start() for m in re.finditer(r'class="stui-content__playlist', html)]
        froms, plays = [], []
        for i, st in enumerate(heads):
            en = heads[i + 1] if i + 1 < len(heads) else len(html)
            blk = html[st:en]
            # 只认真正的选集块（简介区也有 stui-pannel，但里面的"立即播放"按钮不是线路）
            if "stui-content__playlist" not in blk:
                continue
            mn = re.search(r'<h3 class="title">.*?(?:<[^>]+>)*\s*([^<]+)</h3>', blk, re.S)
            lname = self._clean(mn.group(1)) if mn else ""
            eps = []
            for em in re.finditer(r'href="(/vodplay/(\d+)-(\d+)-(\d+)/)"[^>]*>([^<]+)</a>', blk):
                full, vid2, sid, nid, ename = em.groups()
                if str(vid2) != str(vid):   # 过滤掉推荐位串台
                    continue
                ename = self._clean(ename)
                if not ename:
                    ename = "第%s集" % nid
                # 集名去 $ # 防破坏分隔符
                ename = ename.replace("$", "").replace("#", "").strip() or ("第%s集" % nid)
                eps.append("%s$%s" % (ename, "%s@@%s@@%s" % (vid, sid, nid)))
            if not eps:
                continue
            lname = (lname or ("线路" + str(len(froms) + 1))).replace("$$$", "/")
            # 重名线路加后缀，防选集塌陷
            base, k = lname, 1
            while lname in froms:
                k += 1
                lname = "%s%d" % (base, k)
            froms.append(lname)
            plays.append("#".join(eps))
        return froms, plays

    # ---------------- 播放 ----------------
    def _resolve_cipher(self, cipher):
        """密文 -> 真实直链（两步纯 HTTP，已实测）"""
        try:
            page_url = JZ_PIC + "/?url=" + quote(cipher, safe="") + "&ep=1&source_index=1"
            html = self._get(page_url, referer=self.host + "/", timeout=15)
            tok = ""
            if html:
                mt = re.search(r'WGART_PARSE_TOKEN\s*=\s*"([^"]+)"', html)
                if mt:
                    tok = mt.group(1)
            if not tok:
                return ""
            api = (JZ_PIC + "/wgart/api.php?action=try_json_api"
                   + "&video_url=" + quote(cipher, safe="")
                   + "&parse_token=" + quote(tok, safe=""))
            data = self._get_json(api, referer=page_url, timeout=18)
            if isinstance(data, dict) and data.get("success"):
                u = (data.get("data") or {}).get("url") or ""
                if u.startswith("http"):
                    return u
        except Exception:
            return ""
        return ""

    def playerContent(self, flag, id, vipFlags):
        flag = str(flag or "")
        pid = str(id)
        parts = pid.split("@@")
        if len(parts) >= 3:
            vid, sid, nid = parts[0], parts[1], parts[2]
        else:
            m = re.search(r"(\d+)-(\d+)-(\d+)", pid)
            if not m:
                return {"parse": 0, "url": "", "header": {}}
            vid, sid, nid = m.group(1), m.group(2), m.group(3)

        play_url = "%s/vodplay/%s-%s-%s/" % (self.host, vid, sid, nid)
        html = self._get(play_url, referer=self.host + "/")
        raw = ""
        if html:
            m = re.search(r'var\s+player_data\s*=\s*(\{.*?\})\s*</script>', html, re.S)
            if not m:
                m = re.search(r'var\s+player_data\s*=\s*(\{.*?\});', html, re.S)
            if m:
                try:
                    pd = json.loads(m.group(1))
                    raw = str(pd.get("url") or "").strip()
                except Exception:
                    mm = re.search(r'"url"\s*:\s*"([^"]*)"', m.group(1))
                    raw = mm.group(1) if mm else ""

        header = {"User-Agent": self.ua}   # 必须 dict；不带 Referer

        # 1) 密文 -> 解密成直链
        if raw and raw.startswith(_CIPHER_PRE):
            real = self._resolve_cipher(raw)
            if real:
                return {"parse": 0, "url": real, "header": header}
            # 解密失败：回播放页交 App 内建嗅探
            return {"parse": 0, "url": play_url, "header": header}

        # 2) 明文直链
        if raw.startswith("http"):
            lu = raw.lower()
            if any(ext in lu for ext in _MEDIA_EXT):
                return {"parse": 0, "url": raw, "header": header}
            # 3) 外链（youku / qq 等）：回播放页交 App 嗅探
            return {"parse": 0, "url": play_url, "header": header}

        # 4) 什么都没抓到：回播放页交 App 嗅探，绝不崩
        return {"parse": 0, "url": play_url, "header": header}

    # ---------------- 本地代理（四元组契约） ----------------
    def localProxy(self, param=None):
        if isinstance(param, str) and param.strip().startswith("{"):
            try:
                param = json.loads(param)
            except Exception:
                param = None
        return [404, "text/plain", b"not supported", {"Content-Type": "text/plain; charset=utf-8"}]
