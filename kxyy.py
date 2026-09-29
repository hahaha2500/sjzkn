# -*- coding: utf-8 -*-
"""
开心影院 kxyy —— FongMi/TVBox type=3 (Chaquopy Python) 点播源

【站点说明书（2026-09-30 实测）】
  站型  : 苹果CMS v10 + Tabler UI（页脚 程序版本 v6.0.0），纯 HTML 直抓
  接口  : /api.php/provide/vod 返回 "closed" —— 采集接口已关，必须 HTML 直抓
  域名池: www.kxyy1.cc / kxyy1.cc / www.kxyy2.cc / kxyy2.cc 全部实测 200（真影站，109KB/176个voddetail）
          ★ www.kxyy.app 是「官方发布页/跳转页」(26KB, 0个voddetail) —— 非影站，绝不能进池
  URL 规律:
    分类首页  /vodtype/{slug}.html          slug: dianying/dianshiju/zongyi/dongman/duanju
    列表      /vodshow/{tid}-----------.html (12 段位, 第1页页码位留空)
    列表第N页 /vodshow/{tid}--------{N}---.html   ★位[8]=页码
    筛选      位[1]=地区 位[2]=排序(time/hits_week/douban_score) 位[3]=类型 位[11]=年份
    总页数    从尾页 <a>...尾页</a> 的 href 抠（电影 1903 页）
    详情      /voddetail/{id}.html
    播放      /vodplay/{vid}-{sid}-{nid}.html
    搜索      /vodsearch/-------------.html?wd={quote(kw)}
  封面      绝对地址 pic.zhuiying.me / wework.qpic.cn（无需拼接，带 referrerpolicy=no-referrer）
  线路      4 条: sid=2 YX源 / sid=3 BF源 / sid=4 MD源 / sid=1 LZ源（顺序见页面 nav-tabs）
  播放      播放页内嵌 var player_data={...}（encrypt=0）——需【括号平衡扫描】抠 JSON 禁贪婪正则
            实测明文 m3u8:
              sid=1 LZ源 https://v.lzcdn27.com/.../index.m3u8      HTTP 200 + EXT-X-STREAM-INF ✅
              sid=3 BF源 https://fengbao12.com/.../index.m3u8      HTTP 200 (23KB) ✅
              sid=4 MD源 https://play.modujx17.com/.../index.m3u8  HTTP 200（首连易超时需重试）✅
              sid=2 YX源 为 NBY-XMYAES...|... 自定义加密串，服务端无法解 → 交嗅探兜底
  搜索验证  首次搜索返回 403 页「安全验证」图形验证码
            GET  /index.php/verify/index.html          (128x40 调色板 PNG)
            POST /index.php/ajax/verify_check?type=search&verify={code} -> {"code":1} 放行
            ★必须同会话 cookie（requests.Session），放行后重取原 URL 即出结果
            ★OCR 用 ddddocr（try import，手机壳没装则回落本地池，不崩）

【加载层铁律（默影视壳 2026-09-28 事故复盘）】
  getDependence() -> []、显式 __init__ 且先 super().__init__()、
  localProxy(param=None) 四元组 [status,mime,bytes,header]、header 一律 dict、
  T4 双模式 (try import base.spider 失败则裸基类)。
"""

import re
import json
import time
import threading
from urllib.parse import quote

try:  # T4 双模式：FongMi/Chaquopy 走 base.spider，纯客户端壳走裸基类
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def fetch(self, url, headers=None, timeout=10):
            return ""
        def proxyUrl(self, *a, **k):
            return ""


# requests 可选（手机壳可能没装）——缺了走 urllib，不崩
try:
    import requests
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False
    import urllib.request
    import urllib.error


# OCR 可选——缺了回落本地池，不崩
try:
    import ddddocr
    _OCR = ddddocr.DdddOcr(show_ad=False)
    _HAS_OCR = True
except Exception:
    _OCR = None
    _HAS_OCR = False


HOSTS = [
    "https://www.kxyy1.cc",
    "https://kxyy1.cc",
    "https://www.kxyy2.cc",
    "https://kxyy2.cc",
]
# ★ www.kxyy.app 是「官方发布页/跳转页」(title=开心影院-官方最新网址发布页, 0 个 voddetail)
#   不是影站，绝不能进池——探活 200 ≠ 是同一个站，必须验证结构(voddetail 数)。
PUBLISH = "https://www.kxyy.app"

# tid -> (名称, slug)  —— slug 用于 /vodtype/{slug}.html 兜底
CATS = {
    "1":   ("电影", "dianying"),
    "2":   ("电视剧", "dianshiju"),
    "3":   ("综艺", "zongyi"),
    "4":   ("动漫", "dongman"),
    "26":  ("短剧", "duanju"),
    "24":  ("纪录片", "jilupian"),
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_BAD_IMG = ("loading", "default", "placeholder", "blank", "transparent", "1x1", ".svg",
            "img-bj-k", "logo")

_GOOD_HOST = None          # 记住已通的镜像
_GOOD_LOCK = threading.Lock()
_CACHE = {}                # 模块级缓存 —— 壳子可能每次调用都 new Spider，实例级会全废
_CACHE_LOCK = threading.Lock()


def _now():
    return time.time()


def _cache_get(key, ttl):
    with _CACHE_LOCK:
        v = _CACHE.get(key)
        if v and _now() - v[0] < ttl:
            return v[1]
    return None


def _cache_set(key, val, ttl=300):
    with _CACHE_LOCK:
        if len(_CACHE) > 512:
            _CACHE.clear()
        _CACHE[key] = (_now(), val)


def _clean(s):
    """去 HTML 标签/实体/分隔符，防破坏 $ # $$$ 协议"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
           .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    s = re.sub(r"[\s　]+", " ", s).strip()
    for bad in ("$", "#"):
        s = s.replace(bad, "")
    return s


def _bad_pic(u):
    if not u:
        return True
    lu = u.lower()
    return any(b in lu for b in _BAD_IMG)


class Spider(BaseSpider):

    def __init__(self):
        super(Spider, self).__init__()
        self.host = HOSTS[0]
        self.headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session = None
        if _HAS_REQ:
            try:
                self.session = requests.Session()
                self.session.headers.update(self.headers)
                try:
                    from requests.adapters import HTTPAdapter
                    from urllib3.util.retry import Retry
                    try:
                        rt = Retry(total=2, connect=2, read=2, backoff_factor=0.6,
                                   status_forcelist=[429, 500, 502, 503, 504],
                                   allowed_methods=frozenset(["GET", "POST"]))
                    except TypeError:  # urllib3 v1 老版本参数名不同
                        rt = Retry(total=2, connect=2, read=2, backoff_factor=0.6,
                                   status_forcelist=[429, 500, 502, 503, 504],
                                   method_whitelist=frozenset(["GET", "POST"]))
                    ad = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=rt)
                    self.session.mount("https://", ad)
                    self.session.mount("http://", ad)
                except Exception:
                    pass
            except Exception:
                self.session = None
        self._ocr = _OCR

    # ---------- 加载层契约 ----------
    def getDependence(self):
        return []          # 不给宿主加依赖负担（默影视铁律①）

    def getName(self):
        return "开心影院"

    def init(self, extend=""):
        try:
            if isinstance(extend, dict):
                cfg = extend
            elif isinstance(extend, str) and extend.strip():
                cfg = json.loads(extend)
            else:
                cfg = {}
        except Exception:
            cfg = {}
        h = str(cfg.get("host", "") or "").strip()
        if h:
            if not h.startswith("http"):
                h = "https://" + h
            self.host = h.rstrip("/")
        elif _GOOD_HOST:
            self.host = _GOOD_HOST
        self._home_html.cache_reset() if hasattr(self._home_html, "cache_reset") else None

    def isVideoFormat(self, url):
        u = str(url or "").lower().split("?")[0]
        return any(u.endswith(e) for e in (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"))

    def manualVideoCheck(self):
        return False

    # ---------- 网络单一入口 ----------
    def _get(self, url, ref=None, timeout=15, retries=2):
        """统一取 HTML。镜像域名池容灾：DNS/超时/5xx 自动切下一个，通了就记住。"""
        global _GOOD_HOST
        if not url.startswith("http"):
            url = self.host + ("/" + url.lstrip("/") if not url.startswith("/") else url)
        tries = list(HOSTS)
        if self.host in tries:
            tries.remove(self.host)
            tries.insert(0, self.host)
        last = ""
        for host in tries:
            real = re.sub(r"^https?://[^/]+", host, url)
            for k in range(max(1, retries)):
                try:
                    if self.session is not None:
                        r = self.session.get(real, timeout=timeout,
                                             headers={"Referer": ref} if ref else None)
                        code = r.status_code
                        body = r.content
                    else:
                        rq = urllib.request.Request(real)
                        rq.add_header("User-Agent", UA)
                        if ref:
                            rq.add_header("Referer", ref)
                        with urllib.request.urlopen(rq, timeout=timeout) as rr:
                            code, body = rr.status, rr.read()
                    if code == 200 and body:
                        for enc in ("utf-8", "gbk"):
                            try:
                                txt = body.decode(enc)
                                break
                            except Exception:
                                txt = body.decode("utf-8", "ignore")
                        # ★结构验证：必须是影站才认这个域（发布页也有 200+title，只看状态码会误判）
                        is_site = ("voddetail" in txt or "vodplay" in txt or
                                   "vodshow" in txt or "tabler" in txt or
                                   "player_data" in txt)
                        if is_site and (len(body) > 300 or "<title>" in txt):
                            with _GOOD_LOCK:
                                global _GOOD_HOST
                                _GOOD_HOST = host
                            self.host = host
                            return txt
                    last = "HTTP %s" % code
                except Exception as e:
                    last = "%s: %s" % (type(e).__name__, str(e)[:60])
                    time.sleep(0.3)
        return ""

    def _seg(self, html):
        """抠 var player_data={...} —— 括号平衡扫描，禁贪婪正则（记忆：juzong 教训）"""
        if not html:
            return None
        key = "var player_data="
        i = html.find(key)
        if i < 0:
            for alt in ("var player_aaaa=", "player_data ="):
                i = html.find(alt)
                if i >= 0:
                    key = alt
                    break
        if i < 0:
            return None
        s = html[i + len(key):]
        st, depth, end = None, 0, -1
        for j, c in enumerate(s):
            if st is None:
                if c == "{":
                    st, depth = "}", 1
                elif c == "[":
                    st, depth = "]", 1
                elif c == '"':
                    st = '"'
            elif st == '"':
                if c == "\\":
                    continue
                if c == '"':
                    st = None
            elif c == st:
                depth -= 1
            elif c in "{[":
                depth += 1
            if st is None or (st in "}" and depth == 0) or (st in "]" and depth == 0):
                end = j
                break
        if end < 0:
            return None
        raw = s[:end + 1].replace("\\/", "/")
        try:
            return json.loads(raw)
        except Exception:
            m = re.search(r'"url"\s*:\s*"(https?://[^"]+?)"', raw)
            if m:
                return {"url": m.group(1)}
            return None

    # ---------- 首页 ----------
    def _nav(self):
        """动态抓分类（失败回落写死表）"""
        ck = _cache_get("nav", 600)
        if ck:
            return ck
        html = self._home_html()
        found = {}
        if html:
            # slug -> tid 映射（首页导航 href 与文字之间隔着 span，直接按 slug 映射最稳）
            for slug in re.findall(r'href="/vodtype/([a-z_]+)\.html"', html):
                for tid, (cn, cs) in CATS.items():
                    if cs == slug:
                        found[tid] = (cn, slug)
            # 再抓一次「名称」文字（span.nav-link-title 结构）
            for m in re.finditer(
                    r'href="/vodtype/[a-z_]+\.html".{0,400}?nav-link-title[^>]*>([^<]{1,12})<',
                    html, re.S):
                nm = _clean(m.group(1))
                for tid, (cn, cs) in CATS.items():
                    if cn == nm or nm == cn:
                        found.setdefault(tid, (nm or cn, cs))
        for tid, v in CATS.items():
            found.setdefault(tid, v)
        out = [(t, found[t][0]) for t in sorted(found, key=lambda x: int(x))]
        _cache_set("nav", out, 600)
        return out

    def _home_html(self):
        ck = _cache_get("home", 300)
        if ck is not None:
            return ck
        h = self._get("/")
        _cache_set("home", h, 300)
        return h

    def homeContent(self, filter=False):
        cls = [{"type_id": t, "type_name": n} for t, n in self._nav()]
        filters = {
            "1": [{"key": "by", "name": "排序", "value": [
                        {"n": "最新", "v": "time"}, {"n": "热门", "v": "hits_week"},
                        {"n": "评分", "v": "douban_score"}]},
                  {"key": "area", "name": "地区", "value": [
                        {"n": "大陆", "v": "中国大陆"}, {"n": "香港", "v": "中国香港"},
                        {"n": "台湾", "v": "中国台湾"}, {"n": "美国", "v": "美国"},
                        {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"},
                        {"n": "泰国", "v": "泰国"}, {"n": "英国", "v": "英国"},
                        {"n": "法国", "v": "法国"}, {"n": "德国", "v": "德国"}]},
                  {"key": "class", "name": "类型", "value": [
                        {"n": "科幻", "v": "科幻"}, {"n": "剧情", "v": "剧情"},
                        {"n": "惊悚", "v": "惊悚"}, {"n": "爱情", "v": "爱情"},
                        {"n": "动作", "v": "动作"}, {"n": "悬疑", "v": "悬疑"},
                        {"n": "犯罪", "v": "犯罪"}, {"n": "喜剧", "v": "喜剧"},
                        {"n": "奇幻", "v": "奇幻"}, {"n": "战争", "v": "战争"}]},
                  {"key": "year", "name": "年份", "value": [
                        {"n": str(y), "v": str(y)} for y in range(2026, 2013, -1)]}],
        }
        for t in ("2", "3", "4", "26", "24"):
            filters[t] = [filters["1"][0]]
        data = {"class": cls, "list": self.homeVideoContent().get("list", [])}
        if filter:
            data["filters"] = filters
        return data

    # ---------- 卡片解析（列表/搜索/首页共用） ----------
    def _parse_cards(self, html):
        if not html:
            return []
        out, seen = [], set()
        # 结构: <strong class="ribbon...">评分</strong> 可选
        #        <a href="/voddetail/{id}.html" ...><img src="海报"><span class="badge...">备注</span></a>
        #        <div class="card-body..."><h3 ...>标题</h3><p ...>日期/集数</p>
        pat = re.compile(
            r'<a[^>]+href="(/voddetail/(\d+)\.html)"[^>]*>(.*?)</a>\s*'
            r'<div class="card-body[^"]*">\s*<h3[^>]*>(.*?)</h3>(.*?)</div>',
            re.S)
        rib = re.compile(r'ribbon-top[^>]*>([\d.]+)<')
        for m in pat.finditer(html):
            vid, inner, title, tail = m.group(2), m.group(3), m.group(4), m.group(5)
            if vid in seen:
                continue
            title = _clean(title)
            if not title:
                title = _clean(re.search(r'title="([^"]+)"', inner).group(1)) if 'title="' in inner else ""
            if not title:
                continue
            seen.add(vid)
            pic = ""
            im = re.search(r'<img[^>]+src="([^"]+)"', inner)
            if im and not _bad_pic(im.group(1)):
                pic = im.group(1).replace("&amp;", "&")
            remark = ""
            bm = re.search(r'badge[^"]*"[^>]*>([^<]{1,30})<', inner)
            if bm:
                remark = _clean(bm.group(1))
            # 评分/日期（卡片下方 p）
            pm = re.search(r'text-muted[^>]*>([^<]{1,30})<', tail)
            sub = _clean(pm.group(1)) if pm else ""
            if not remark and sub:
                remark = sub
            elif sub and sub not in remark:
                remark = (remark + " " + sub).strip()
            # 分数 ribbon 挪到备注
            pre = html[max(0, m.start() - 220):m.start()]
            rmb = rib.search(pre)
            if rmb and rmb.group(1) not in remark:
                remark = (remark + " " + rmb.group(1)).strip()
            out.append({"vod_id": vid, "vod_name": title, "vod_pic": pic,
                        "vod_remarks": remark[:40]})
        # 兜底 1：搜索页是列表式结构（<a ... href=detail title="片名">文本</a> + 前置 <img>），
        #         且首个匹配常是「只装 img 的空标题链接」——★必须拿到非空标题才登记 seen，
        #         否则会把自己后面的有效链接跳过（本项目踩过的坑）。
        if not out:
            for m in re.finditer(r'<a[^>]+href="/voddetail/(\d+)\.html"[^>]*>(.*?)</a>',
                                 html, re.S):
                vid = m.group(1)
                if vid in seen:
                    continue
                title = _clean(m.group(2))
                if not title:
                    continue
                seen.add(vid)
                pre = html[max(0, m.start() - 520):m.start()]
                im = re.search(r'<img[^>]+src="(https?://[^"]+)"', pre)
                pic = im.group(1) if im and not _bad_pic(im.group(1)) else ""
                post = html[m.end():m.end() + 1000]
                rm = re.search(r'<strong>(?:类型|备注|又名)：</strong>([^<]{1,40})', post)
                out.append({"vod_id": vid, "vod_name": title, "vod_pic": pic,
                            "vod_remarks": _clean(rm.group(1)) if rm else ""})
        return out[:48]

    def homeVideoContent(self):
        ck = _cache_get("hv", 300)
        if ck is not None:
            return {"list": ck}
        html = self._home_html()
        items = self._parse_cards(html) if html else []
        if not items and html:  # 首页区块结构不同，退化抓 voddetail+标题
            seen, items = set(), []
            for m in re.finditer(
                    r'<a[^>]+href="/voddetail/(\d+)\.html"[^>]*>(.*?)</a>(.*?)'
                    r'<h3[^>]*>(.*?)</h3>', html, re.S):
                vid = m.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                title = _clean(m.group(4))
                if not title:
                    continue
                im = re.search(r'<img[^>]+src="([^"]+)"', m.group(2))
                pic = im.group(1) if im and not _bad_pic(im.group(1)) else ""
                bm = re.search(r'badge[^"]*"[^>]*>([^<]{1,30})<', m.group(2))
                items.append({"vod_id": vid, "vod_name": title, "vod_pic": pic,
                              "vod_remarks": _clean(bm.group(1)) if bm else ""})
                if len(items) >= 40:
                    break
        _cache_set("hv", items, 300)
        return {"list": items}

    # ---------- 分类 ----------
    def _list_url(self, tid, pg, ext=None):
        """12 段位: [0]tid [1]area [2]by [3]class [4]lang [5]letter [6]year
           [7]空 [8]页码 [9]空 [10]空 [11]年份(上映)   ★位[8]=页码"""
        ext = ext or {}
        seg = [""] * 12
        seg[0] = str(tid)
        if ext.get("area"):
            seg[1] = quote(str(ext["area"]))
        if ext.get("by"):
            seg[2] = str(ext["by"])
        if ext.get("class"):
            seg[3] = quote(str(ext["class"]))
        if int(pg or 1) > 1:
            seg[8] = str(int(pg))
        if ext.get("year"):
            seg[11] = str(ext["year"])
        return "/vodshow/%s.html" % "-".join(seg)

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        tid = str(tid)
        try:
            pg = int(str(pg or 1))
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        ext = dict(extend or {})
        ck = "cat_%s_%s_%s" % (tid, pg, json.dumps(ext, sort_keys=True, ensure_ascii=False))
        hit = _cache_get(ck, 300)
        if hit is not None:
            return hit
        html = self._get(self._list_url(tid, pg, ext))
        items = self._parse_cards(html)
        # 总页数：尾页 href
        pagecount = 1
        if html:
            m = re.search(r'href="/vodshow/\d+-*?(\d+)-*-\.html"[^>]*>\s*尾页', html)
            if not m:
                m = re.search(r'href="/vodshow/([^-]+(?:-[^-]*)*?)-(\d+)-*-\.html"[^>]*>\s*尾页', html)
            if m:
                try:
                    g = m.group(1) if m.lastindex and m.lastindex >= 2 else m.group(1)
                    nums = re.findall(r"(\d+)", g)
                    pagecount = max(int(nums[-1]), 1)
                except Exception:
                    pagecount = 1
            if pagecount <= 1:
                mm = re.findall(r'/vodshow/\d+(-+)(\d+)(-+)\.html', html)
                for _a, n, _b in mm:
                    try:
                        pagecount = max(pagecount, int(n))
                    except Exception:
                        pass
        # 空页刹车：当前页无数据则说明到底了（防无限空翻）
        if not items and pg > 1:
            pagecount = pg
        pagecount = max(1, min(int(pagecount), 100000))
        res = {"page": pg, "pagecount": pagecount, "limit": 48,
               "total": pagecount * 48, "list": items}
        _cache_set(ck, res, 300)
        return res

    # ---------- 详情 ----------
    def _parse_detail(self, html, vid):
        info = {}
        if not html:
            return info
        # 标题 <h1>片名 (2026)</h1>
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        name = _clean(m.group(1)) if m else ""
        name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
        if not name:
            m = re.search(r"<title>《([^》]+)》", html)
            name = _clean(m.group(1)) if m else ""
        info["vod_name"] = name
        # 海报：带 alt=片名的 img，其次 og:image
        pic = ""
        im = re.search(r'<img[^>]+src="(https?://[^"]+)"[^>]*alt="%s"' % re.escape(name), html) if name else None
        if not im:
            im = re.search(r'<img[^>]+alt="%s"[^>]+src="(https?://[^"]+)"' % re.escape(name), html) if name else None
        if not im:
            im = re.search(r'property="og:image"\s+content="(https?://[^"]+)"', html)
        if im and not _bad_pic(im.group(1)):
            pic = im.group(1).replace("&amp;", "&")
        info["vod_pic"] = pic
        # 备注 / 评分
        rm = re.search(r'badge[^"]*"[^>]*>摘要：\s*<span[^>]*>([^<]{1,40})', html)
        if not rm:
            rm = re.search(r'摘要：</strong>\s*<span[^>]*>([^<]{1,40})', html)
        info["vod_remarks"] = _clean(rm.group(1)) if rm else ""
        sc = re.search(r"豆瓣评分：\s*([^<]{1,12})", html)
        if sc and "暂无" not in sc.group(1):
            info["vod_score"] = _clean(sc.group(1))
        # 导演/主演
        def _field(label):
            # ★主演列表实测跨度 5810 字符（50 个演员链接），上限必须给足（取 9000）
            mm = re.search(r"<strong>%s：</strong>(.{0,9000}?)</p>" % label, html, re.S)
            if mm:
                vals = re.findall(r'>([^<>]{1,30})</a>', mm.group(1))
                if not vals:
                    vals = [_clean(mm.group(1))]
                return " / ".join([_clean(v) for v in vals if _clean(v)][:10])
            return ""
        info["vod_director"] = _field("导演")
        info["vod_actor"] = _field("主演")
        # 字段提取用去标签纯文本（原 HTML 是 <strong>标签：</strong>值，直接匹配冒号会失败）
        plain = re.sub(r"<[^>]+>", " ", html)
        plain = plain.replace("&nbsp;", " ").replace("&amp;", "&")
        ym = re.search(r"首播：\s*(\d{4})", plain) or re.search(r"[（(](\d{4})[）)]", html)
        info["vod_year"] = ym.group(1) if ym else ""
        am = re.search(r"制片国家/地区：\s*\[?([^\]<]{1,20})", plain)
        info["vod_area"] = _clean(am.group(1)) if am else ""
        cl = re.search(r"类型：\s*([^\s<]{1,40})", plain)
        info["type_name"] = _clean(cl.group(1)) if cl else ""
        # 简介
        cm = re.search(r"剧情简介：(.{20,1800}?)(?:</|\"><)", html, re.S)
        if not cm:
            cm = re.search(r'itemprop="description"[^>]*content="([^"]{20,800})"', html)
            desc = cm.group(1) if cm else ""
        else:
            desc = cm.group(1)
        desc = _clean(desc).replace("&amp; nbsp;", " ").replace("nbs p;", " ")
        desc = re.sub(r"(?:&amp;)+\s*nbs\s*p;?", " ", desc)
        info["vod_content"] = desc[:900]
        # ---- 线路 + 集数 ----
        lines = {}
        for m in re.finditer(r'href="#tabs-home-(\d+)"[^>]*>(.*?)</a>', html, re.S):
            sid = m.group(1)
            nm = re.sub(r"[\s&nbsp;]+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
            nm = re.sub(r"\s+\d+$", "", nm).strip()
            lines[sid] = nm or ("线路" + sid)
        eps = {}
        for m in re.finditer(r'/vodplay/(\d+)-(\d+)-(\d+)\.html', html):
            if m.group(1) != str(vid):
                continue
            eps.setdefault(m.group(2), set()).add(int(m.group(3)))
        # 兜底：没有 nav-tabs 时用 sid 序号
        for sid in eps:
            lines.setdefault(sid, "线路" + sid)
        froms, urls = [], []
        movie = len(eps) <= 1 and all(
            (max(v) if v else 1) <= 1 for v in eps.values())
        for sid in sorted(lines, key=lambda x: int(x)):
            if sid not in eps:
                continue
            nos = sorted(eps[sid])
            nm = lines[sid]
            segs = []
            for n in nos:
                label = "正片" if movie else "第%02d集" % n
                segs.append("%s$%s@@%s@@%s@@%s" % (label, "main", vid, sid, n))
            if not segs:
                continue
            froms.append("开心·%s" % nm)
            urls.append("#".join(segs))
        # 加密线路（sid=2 YX源 NBY-XMYAES）另挂嗅探线
        for sid in sorted(lines, key=lambda x: int(x)):
            if sid not in eps:
                continue
            nos = sorted(eps[sid])
            nm = lines[sid]
            segs = []
            for n in nos:
                label = "正片" if movie else "第%02d集" % n
                segs.append("%s$%s@@%s@@%s@@%s" % (label, "sniff", vid, sid, n))
            froms.append("开心·%s·嗅探" % nm)
            urls.append("#".join(segs))
        info["vod_play_from"] = "$$$".join(froms)
        info["vod_play_url"] = "$$$".join(urls)
        return info

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        out = []
        for one in ids:
            vid = str(one)
            if "@@" in vid:
                vid = vid.split("@@")[0]
            ck = "det_%s" % vid
            info = _cache_get(ck, 600)
            if info is None:
                html = self._get("/voddetail/%s.html" % vid, ref=self.host + "/")
                info = self._parse_detail(html, vid) if html else {}
                if info:
                    _cache_set(ck, info, 600)
            if info:
                info["vod_id"] = vid
                out.append(info)
            else:
                # 诊断卡兜底（不给白屏）
                out.append({
                    "vod_id": vid, "vod_name": "[诊断] 详情抓取失败",
                    "vod_pic": "",
                    "vod_content": "域名 %s /voddetail/%s.html 抓空。请换镜像域 extend:{\"host\":\"www.kxyy.app\"}，或稍后重试。"
                                   % (self.host, vid),
                    "vod_play_from": "", "vod_play_url": "",
                })
        return {"list": out}

    # ---------- 搜索 ----------
    def _verify_search(self, page_url):
        """图形验证码：拉图 -> OCR -> POST verify_check -> 放行。同会话 cookie 必须保住。"""
        if not _HAS_OCR or self.session is None:
            return False
        for _ in range(8):
            try:
                r = self.session.get(self.host + "/index.php/verify/index.html?r=%d" % int(time.time() * 1000),
                                     timeout=12, headers={"Referer": page_url})
                code = self._ocr.classification(r.content)
                if not code or not (3 <= len(code) <= 6) or not code.isalnum():
                    continue
                rp = self.session.post(
                    self.host + "/index.php/ajax/verify_check?type=search&verify=" + quote(code),
                    timeout=12, headers={"Referer": page_url, "X-Requested-With": "XMLHttpRequest"})
                try:
                    res = rp.json()
                except Exception:
                    res = {}
                if res.get("code") == 1:
                    return True
            except Exception:
                time.sleep(0.3)
        return False

    def _local_pool(self, key, pg):
        """OCR 不可用/验证码总失败时的本地池：把热门分类页当搜索结果（不崩）"""
        words = [key] + [w for w, _t in self._nav()[:4]]
        seen, out = set(), []
        for w in words:
            for t, _n in self._nav()[:5]:
                html = self._get(self._list_url(t, pg))
                for it in (self._parse_cards(html) or []):
                    if it["vod_id"] in seen:
                        continue
                    nm = it.get("vod_name", "")
                    if w == key and key and key not in nm:
                        continue
                    seen.add(it["vod_id"])
                    out.append(it)
                if len(out) >= 24:
                    break
            if len(out) >= 24:
                break
        return out[:30]

    def searchContent(self, key, quick, pg="1"):
        key = _clean(str(key or "")).strip()
        pg = int(pg or 1)
        ck = "s_%s_%s" % (key, pg)
        hit = _cache_get(ck, 180)
        if hit is not None:
            return hit
        url = "/vodsearch/-------------.html?wd=" + quote(key)
        if pg > 1:
            url = "/vodsearch/%s----------%d---.html?wd=%s" % (quote(key), pg, quote(key))
        full = self.host + url
        html = self._get(full, ref=self.host + "/")
        items = self._parse_cards(html) if html else []
        # 命中验证码页
        if html and "安全验证" in html and "verifyCode" in html:
            ok = self._verify_search(full)
            if ok:
                html = self._get(full, ref=self.host + "/")
                items = self._parse_cards(html) if html else []
            if not items:
                items = self._local_pool(key, pg)
        if not items and pg > 1:
            res = {"page": pg, "pagecount": pg, "limit": 30, "total": 0, "list": []}
        else:
            res = {"page": pg, "pagecount": pg + (1 if items else 0), "limit": 30,
                   "total": len(items), "list": items}
        _cache_set(ck, res, 180)
        return res

    # ---------- 播放 ----------
    def _resolve(self, vid, sid, nid):
        """播放页 -> player_data.url 明文 m3u8/mp4。拿不到返回 "" 让上层降级嗅探。"""
        ck = "pl_%s_%s_%s" % (vid, sid, nid)
        hit = _cache_get(ck, 60)
        if hit is not None:
            return hit
        url = ""
        for attempt in range(2):   # MD源首连易超时，重试一次
            html = self._get("/vodplay/%s-%s-%s.html" % (vid, sid, nid),
                             ref=self.host + "/voddetail/%s.html" % vid, timeout=18)
            if not html:
                time.sleep(0.4)
                continue
            d = self._seg(html)
            if d and isinstance(d, dict):
                u = str(d.get("url") or "").strip()
                # 加密串（NBY-XMYAES...|...）服务端无解，交给嗅探
                if u.startswith("http") and not u.startswith("javascript"):
                    url = u
                    break
                if u and not u.startswith("http"):
                    # 可能是相对/其它格式，尝试还原转义
                    u2 = u.replace("\\/", "/")
                    if u2.startswith("http"):
                        url = u2
                        break
                    url = ""
                    break
            # 兜底：页面裸 m3u8
            m = re.search(r'https?://[^"\'\\\s<>]+?\.(?:m3u8|mp4)(?:\?[^"\'\\\s<>]*)?', html)
            if m:
                url = m.group(0).replace("\\/", "/")
                break
            time.sleep(0.3)
        _cache_set(ck, url, 60)
        return url

    def playerContent(self, flag, id, vipFlags):
        parts = str(id or "").split("@@")
        # 兼容旧 3 段式 / 裸 id
        if len(parts) == 4:
            mode, vid, sid, nid = parts
        elif len(parts) == 3:
            mode, vid, sid = parts
            nid = 1
        elif len(parts) == 2:
            mode, vid = parts
            sid, nid = 1, 1
        else:
            mode, vid, sid, nid = "main", parts[0], 1, 1
        flag = str(flag or "")
        # 线路名可覆盖模式
        if "嗅探" in flag:
            mode = "sniff"
        elif "原页" in flag:
            mode = "page"
        purl = "/vodplay/%s-%s-%s.html" % (vid, sid, nid)
        real = self._resolve(vid, sid, nid)

        if mode == "sniff" or (mode == "main" and not real):
            # 加密线路 / 取不到直链 -> 回播放页，交 App 内建嗅探
            return {"parse": 0, "url": self.host + purl, "header": dict(self.headers)}
        if mode == "page":
            return {"parse": 0, "url": self.host + purl, "header": dict(self.headers)}
        if real:
            hdr = {"User-Agent": UA, "Referer": self.host + "/"}
            return {"parse": 0, "url": real, "header": hdr}
        # 最后兜底：parse 1 交宿主解析
        return {"parse": 1, "url": self.host + purl, "header": dict(self.headers)}

    # ---------- 本地代理 ----------
    def localProxy(self, param=None):
        # 契约四元组 [status, mime, bytes, header]
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except Exception:
                param = None
        if isinstance(param, dict):
            u = param.get("url") or param.get("u") or ""
            if u:
                try:
                    if self.session is not None:
                        r = self.session.get(u, timeout=15)
                        body = r.content
                        code = r.status_code
                    else:
                        rq = urllib.request.Request(u)
                        rq.add_header("User-Agent", UA)
                        rq.add_header("Referer", self.host + "/")
                        with urllib.request.urlopen(rq, timeout=15) as rr:
                            code, body = rr.status, rr.read()
                    mime = ("application/vnd.apple.mpegurl"
                            if ".m3u8" in u.lower() else "application/octet-stream")
                    return [code, mime, body, dict(self.headers)]
                except Exception:
                    pass
        return [404, "text/plain", b"not found", dict(self.headers)]
