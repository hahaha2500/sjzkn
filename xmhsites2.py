# -*- coding: utf-8 -*-
"""站群聚合源 —— 泄密合集社同族（几百个 MacCMS 站聚合）

站点逆向要点（2026-10-02 实测）
--------------------------------------------------------------------------------
1) 这是一整个【站群】：主站首页挂 14 个"导航页"入口，导航页里各有 18~450 个同族站链接，
   6 个导航页就挖出 1250+ 候选，约 80% 是同结构 MacCMS 影视站。
2) 同族站统一结构（与主站 xmhjs5.py 一致）：
   - 页面全在 /cn/home/web/index.php/vod/ 下（常规 /index.php/vod/ 404）
   - ★入口路径 ≠ 站内链接前缀：入口 https://xxx.com/abc/ 而站内链接是 /cn/home/web/...
     → 必须跟站内链接走，用 base={域名根} 拼 URL（主站踩过这个坑，差点全灭）
   - 采集接口已关 → 只能 HTML 直抓
   - ★没有详情页，卡片 href 直接是 /vod/play/id/{vid}/sid/{sid}/nid/{nid}.html
   - 列表页 = 分类页 /vod/type/id/{tid}/page/{pg}.html
   - 播放页 var player_data={...,"encrypt":0,"url":"明文m3u8"} → parse:0 直连
   - 单集单线路（只有 sid=1/nid=1 有 url）
3) 域池用法：★不是容灾而是【内容池】（每个站内容不同）→ 分类页并发抓多个站合并去重，
   每条卡片带"来自哪个站"备注，翻页自动换站池。
"""
import json
import re
import base64
import time
import random
import threading
import traceback
import concurrent.futures as _cf

try:
    from base.spider import Spider as _Base
except Exception:
    class _Base(object):
        pass

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; U; Android 12; zh-cn) AppleWebKit/537.36 Version/4.0 Chrome/107.0 Mobile Safari/537.36",
]
WEB = "/cn/home/web/index.php/vod"
SITE = "站群聚合"
CLS = [("20","最新主播网红"),("21","最新偷拍自拍"),("22","最新人妻熟女"),
       ("23","最新强奸乱伦"),("24","最新制服丝袜"),("25","最新自慰变态"),
       ("26","最新国产精品"),("27","最新亚洲情色"),("28","最新卡通动漫"),
       ("29","最新三级伦理"),("30","最新欧美精品")]
BADIMG = ("placeholder","loading","default","1x1","blank","spacer")
STATIONS = []          # [(base, name)] 运行时填充（见文件末尾）
_SESSION = None
_LOCK = threading.Lock()
_CACHE = {}
_ST = type("S", (), {"span": 6, "ratio": 0.5, "sites": 6})()


def _sess():
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            s = None
            try:
                import requests
                s = requests.Session()
                a = requests.adapters.HTTPAdapter(pool_connections=12, pool_maxsize=24, max_retries=1)
                s.mount("http://", a); s.mount("https://", a)
            except Exception:
                pass
            if s is not None:
                _SESSION = s; return s
            import urllib.request
            _SESSION = urllib.request
            return s
        return _SESSION


def _headers():
    return {"User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9"}


def _get(base, path, nocache=False):
    """站池取页：短超时 + 重试 + TTL 缓存（nocache 用于播放页等时效资源）"""
    ck = base + path
    hit = None if nocache else _CACHE.get(ck)
    if hit and time.time() - hit[0] < 150:
        return hit[1]
    txt = ""
    for _ in range(2):
        try:
            s = _sess()
            if hasattr(s, "get"):
                r = s.get(base + path, headers=_headers(), timeout=(5, 12))
                if r.status_code != 200:
                    continue
                txt = r.text
            else:
                import urllib.request
                req = urllib.request.Request(base + path, headers=_headers())
                with urllib.request.urlopen(req, timeout=12) as fp:
                    txt = fp.read().decode("utf-8", "replace")
            if txt and "页面迷路了" not in txt[:4000]:
                if not nocache:
                    _CACHE[ck] = (time.time(), txt)
                return txt
        except Exception:
            time.sleep(0.3)
    return txt


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
    return re.sub(r"\s{2,}", " ", t.replace("$", "").replace("#", "").strip())


def _grid_style():
    sp = int(getattr(_ST, "span", 6) or 6)
    rt = float(getattr(_ST, "ratio", 0.5) or 0)
    if sp < 0:
        return {}
    st = {"type": 0, "span": sp}
    if rt > 0:
        st["ratio"] = rt
    return st


RE_CARD = re.compile(
    r'(<a[^>]+href="[^"]*vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)\.html\s*"[^>]*>)(.{0,900}?)</a>((?:(?!<a\b[^>]*>).){0,260})?', re.S)
# 占位图黑名单（宁窄勿宽）：只挡确凿的占位，绝不误杀真图
BADIMG = ("loading.gif", "placeholder", "default_", "/default", "spacer", "blank.",
          "1x1.", "noimage", "null.png", "/img/grey")
RE_A_TITLE = re.compile(r'^<a[^>]*?\stitle=["\']([^"\']{2,200})["\']', re.I)


def _pic_of(inner, href_attrs):
    """取图：a 自身属性 → img 的 data-original/data-src/... → img src（跳过占位图）
    同站群实测 5 种模板：有的真图直接在 a 的 data-original，有的 src 是 loading.gif 真图在 data-original"""
    for a in ("data-original", "data-src", "data-lazy-src", "data-poster",
              "data-original-src", "lay-src", "data-background", "data-bg",
              "background-image", "data-echo"):
        m = re.search(a + r'="([^"]+)"', inner) or re.search(a + r'="([^"]+)"', href_attrs)
        if m and m.group(1).strip():
            v = m.group(1).strip()
            if not any(b in v.lower() for b in BADIMG):
                return v
    m = re.search(r'<img[^>]+src="([^"]+)"', inner)
    if m:
        v = m.group(1).strip()
        if v and not any(b in v.lower() for b in BADIMG):
            return v
    # CSS 值形式：style="background-image: url(https://...)"
    m = re.search(r'background-image\s*:\s*url\(\s*["\']?(https?://[^)"\']+)', href_attrs or "")
    if m and not any(b in m.group(1).lower() for b in BADIMG):
        return m.group(1).strip()
    # 全被占位挡了 → 退回第一个候选，宁可给占位也别给空
    m = re.search(r'(?:data-original|data-src|lay-src|data-background|src)="(https?://[^"]+)"', inner)
    if not m:
        m = re.search(r'background-image\s*:\s*url\(\s*["\']?(https?://[^)"\']+)', inner)
    return m.group(1).strip() if m else ""


RE_TITLE_PATS = [
    r'<span[^>]*class="[^"]*video-title[^"]*"[^>]*>(.*?)</span>',
    r'<p[^>]*class="[^"]*\bpname\b[^"]*"[^>]*>(.*?)</p>',
    r'<span[^>]*class="[^"]*\bs_tit\b[^"]*"[^>]*>(?:<em>)?(.*?)(?:</em>)?</span>',
    r'<h[234][^>]*>(.*?)</h[234]>',
    r'<(?:div|span|p|h[1-6])[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</(?:div|span|p|h[1-6])>',
    r'<span[^>]*class="[^"]*fed-list-title[^"]*"[^>]*>(.*?)</span>',
    r'<span[^>]*class="[^"]*\bname\b[^"]*"[^>]*>(.*?)</span>',
]


def _title_of(inner, atitle, imgalt):
    if atitle:
        return _clean(atile)
    for pat in RE_TITLE_PATS:
        m = re.search(pat, inner, re.S | re.I)
        if m:
            t = _clean(m.group(1))
            if t and not t.isdigit():
                return t
    if imgalt and len(imgalt) > 1:
        return _clean(imgalt)
    # 最后兜底：a 内的裸文本（剥掉 img/br/短 span，剩下最长的那段就是片名）
    txt = re.sub(r'<img[^>]*>|<br\s*/?>|</?(?:span|em|i|div)[^>]*>', " ", inner)
    parts = [x.strip() for x in txt.split("\n") if len(x.strip()) >= 4]
    if parts:
        parts.sort(key=len, reverse=True)
        best = _clean(parts[0])
        if 4 <= len(best) <= 120:
            return best
    return ""


def _cards(base, name, html):
    """同一张卡片常有【两个 a】：标题 a（无图）+ 图片 a（有 data-original），
    谁先匹配到不定 → 用 vid 做 key 合并，规则：有图 > 无图、有名 > 无名。"""
    out, idx = [], {}
    for m in RE_CARD.finditer(html or ""):
        atag, vid, sid, nid, inner, outer = (list(m.groups()) + ["", ""])[:6]
        inner = inner or ""
        outer = outer or ""
        atitle = ""
        am = RE_A_TITLE.match(inner[:inner.find(">") + 1]) if inner.startswith("<a") else None
        if am:
            atitle = am.group(1)
        a_attrs = atag or (inner[:inner.find(">") + 1] if ">" in inner else inner[:300])
        alt = ""
        ia = re.search(r'<img[^>]+alt="([^"]*)"', inner)
        if ia:
            alt = ia.group(1)
        title = _title_of(inner, atitle, alt)
        if not title and outer:
            title = _title_of(outer, "", "")
        if not title:
            title = vid
        pic = _pic_of(inner, a_attrs)
        note = ""
        rm = re.search(r'class="[^"]*(?:label-private|duration|pstar|score)[^"]*"[^>]*>\s*([^<]{1,12})', inner)
        if rm:
            note = _clean(rm.group(1))
        card = {"_b": base, "_v": vid, "_s": sid, "_n": nid,
                "_t": title[:120], "_p": pic, "_r": (name or "")[:16], "_m": note[:10]}
        k = base + vid
        old = idx.get(k)
        if old is None:
            idx[k] = len(out)
            out.append(card)
        else:
            o = out[old]
            if (not o["_p"]) and card["_p"]:
                o["_p"] = card["_p"]
            if (not o["_t"] or o["_t"] == vid) and card["_t"] and card["_t"] != vid:
                o["_t"] = card["_t"]
            if (not o["_m"]) and card["_m"]:
                o["_m"] = card["_m"]
    return out


RE_PG = re.compile(r'href="[^"]*vod/type/id/\d+/page/(\d+)\.html"')


class Spider(_Base):
    """分类 = 一个站（110 个同族站站池），每个分类是该站自己的内容与分页。
    extend: {"span":6,"ratio":0.5,"mix":5} —— mix=首页混合几个站；span/ratio 控制版式"""

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
        self.cfg = {}
        ex = (extend or "").strip()
        if ex.startswith("{"):
            try:
                cfg = json.loads(ex)
            except Exception:
                cfg = {}
        elif ex:
            cfg = {}
        try:
            _ST.span = int(cfg.get("span", 6))
        except Exception:
            _ST.span = 6
        try:
            _ST.ratio = float(cfg.get("ratio", 0.5))
        except Exception:
            _ST.ratio = 0.5
        try:
            _ST.mix = int(cfg.get("mix", 5))
        except Exception:
            _ST.mix = 5
        global STATIONS
        if cfg.get("stations"):
            STATIONS = cfg["stations"]
        if not STATIONS:
            STATIONS = _DEF

    def getName(self):
        return SITE

    def _base(self, host):
        return "https://" + host

    # ------------------------------------------------------------ 首页
    def homeContent(self, filter):
        cls = []
        for i, row in enumerate(STATIONS):
            p = row.split("|")
            cls.append({"type_id": str(i), "type_name": p[0],
                        "type_des": "%s · %s卡" % (p[2] if len(p) > 2 else "", "多")})
        return {"class": cls}

    def homeVideoContent(self):
        try:
            rows = random.sample(STATIONS, min(_ST.mix, len(STATIONS)))
            out = []
            with _cf.ThreadPoolExecutor(max_workers=min(5, len(rows))) as ex:
                for r in ex.map(self._one_site_first, rows):
                    out.extend(r)
            return {"list": out[:30]}
        except Exception:
            return {"list": []}

    def _one_site_first(self, row):
        try:
            p = row.split("|")
            h = _get(self._base(p[0]), "%s/type/id/%s/page/1.html" % (WEB, p[1]))
            return [self._mk(x) for x in _cards(self._base(p[0]), p[0], h)]
        except Exception:
            return []

    # ------------------------------------------------------------ 分类（一个站一页）
    def categoryContent(self, tid, pg, filter, extend):
        try:
            i = int(tid)
            if i < 0 or i >= len(STATIONS):
                return {"page": 1, "pagecount": 1, "limit": 0, "total": 0, "list": []}
            row = STATIONS[i].split("|")
            p = max(1, int(pg))
            base = self._base(row[0])
            h = _get(base, "%s/type/id/%s/page/%d.html" % (WEB, row[1], p))
            lst = [self._mk(x) for x in _cards(base, row[0], h)]
            ps = [int(x) for x in RE_PG.findall(h)]
            pc = max(ps) if ps else 1
            if p >= pc:
                pc = p
            # ★站会时好时坏：本分类空 → 自动换同族其它站补内容（备注标真实来源）
            if not lst and p == 1:
                alt = random.sample(STATIONS, min(3, len(STATIONS)))
                for rw in alt:
                    if rw == STATIONS[i]:
                        continue
                    pr = rw.split("|")
                    h2 = _get(self._base(pr[0]), "%s/type/id/%s/page/1.html" % (WEB, pr[1]))
                    c2 = _cards(self._base(pr[0]), pr[0], h2)
                    if c2:
                        lst = [self._mk(x) for x in c2]
                        ps2 = [int(x) for x in RE_PG.findall(h2)]
                        pc = max(ps2) if ps2 else 1
                        break
            return {"page": p, "pagecount": pc, "limit": max(60, len(lst)),
                    "total": 0, "list": lst}
        except Exception:
            print("[站群] category错误:", traceback.format_exc()[-200:])
            return {"page": 1, "pagecount": 1, "limit": 0, "total": 0, "list": []}

    def _mk(self, x):
        return {"vod_id": "p@@%s@@%s@@%s@@%s@@%s@@%s" % (
                    x["_b"].split("//")[-1], x["_v"], x["_s"], x["_n"],
                    _b64e(x["_t"]), _b64e(x["_p"])),
                "vod_name": x["_t"][:120],
                "vod_pic": x["_p"],
                "vod_remarks": (x["_m"] or x["_r"])[:16],
                "style": dict(_grid_style())}

    # ------------------------------------------------------------ 搜索（并发多站）
    def searchContent(self, key, quick, pg="1"):
        try:
            from urllib.parse import quote
        except Exception:
            from urllib import quote
        p = max(1, int(pg or 1))
        n = max(3, min(_ST.mix + 3, 12))
        rows = random.sample(STATIONS, min(n, len(STATIONS)))
        out = []
        try:
            with _cf.ThreadPoolExecutor(max_workers=min(8, len(rows))) as ex:
                for r in ex.map(lambda rw: self._srch(rw, key, p), rows):
                    out.extend(r)
        except Exception:
            pass
        seen = set()
        lst = []
        for it in out:
            k = it["vod_name"][:24] + str(len(it["vod_name"]))
            if k in seen:
                continue
            seen.add(k)
            lst.append(it)
        return {"list": lst, "page": p, "pagecount": p + 1 if len(lst) >= 40 else p,
                "limit": 60, "total": 0}

    def _srch(self, row, key, p):
        try:
            from urllib.parse import quote
        except Exception:
            from urllib import quote
            return []
        try:
            pr = row.split("|")
            base = self._base(pr[0])
            h = _get(base, "%s/search/page/%d.html?wd=%s" % (WEB, p, quote(key)))
            return [self._mk(x) for x in _cards(base, pr[0], h)]
        except Exception:
            return []

    # ------------------------------------------------------------ 详情
    def detailContent(self, ids):
        try:
            raw = ids[0] if isinstance(ids, (list, tuple)) else ids
            parts = str(raw).split("@@")
            if len(parts) < 6 or parts[0] != "p":
                return {"list": []}
            host, vid, sid, nid = parts[1], parts[2], parts[3], parts[4]
            title, pic = _b64d(parts[5]), _b64d(parts[6])
            purl = "%s/play/id/%s/sid/%s/nid/%s.html" % (WEB, vid, sid, nid)
            if (not title or title.isdigit()) and self.cfg.get("tfix", 1):
                try:   # 列表没抓到名 → 用播放页补（每部只多 1 个请求）
                    hp = _get("https://" + host, purl, nocache=True)
                    for pat in (r'<h[1-3][^>]*>([^<]{4,120})</h[1-3]>',
                                r'class="[^"]*\btitle\b[^"]*"[^>]*>([^<]{4,120})<',
                                r'itemprop="name"[^>]*>([^<]{4,120})<'):
                        mm = re.search(pat, hp or "", re.I)
                        if mm:
                            title = _clean(mm.group(1))
                            break
                    if not pic:
                        im = re.search(r'<img[^>]+src="(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', hp or "")
                        pic = im.group(1) if im else ""
                except Exception:
                    pass
            play = "https://%s%s" % (host, purl)
            return {"list": [{"vod_id": raw, "vod_name": (title or vid)[:120],
                               "vod_pic": pic, "vod_remarks": "正片",
                               "vod_content": "来源站：%s\n站方无详情页（点击直达播放页）。" % host,
                               "vod_play_from": "%s·直连$$$%s·嗅探" % (SITE, SITE),
                               "vod_play_url": "正片$%s$$$正片$%s@@sniff" % (play, purl)}]}
        except Exception:
            print("[站群] detail错误:", traceback.format_exc()[-200:])
            return {"list": []}

    # ------------------------------------------------------------ 播放
    def playerContent(self, flag, id, vipFlags):
        try:
            raw = str(id or "")
            sniff = raw.endswith("@@sniff")
            purl = raw[:-len("@@sniff")] if sniff else raw
            if purl.startswith("http"):
                m = re.search(r"https?://([^/]+)", purl)
                host = m.group(1) if m else ""
                path = purl.split(host, 1)[-1]
            else:
                return {"parse": 0, "url": purl, "header": dict(_headers())}
            h = _get("https://" + host, path, nocache=True)
            m = re.search(r'var\s+player_data\s*=\s*(\{.*?\})\s*</script>', h or "", re.S)
            if m:
                try:
                    d = json.loads(m.group(1).replace("\\/", "/"))
                except Exception:
                    d = {}
                u = (d.get("url") or "").strip()
                if u and not sniff:
                    if u.startswith("//"):
                        u = "https:" + u
                    return {"parse": 0, "url": u, "header": dict(_headers())}
            if sniff:
                return {"parse": 0, "url": purl, "header": dict(_headers())}
            for rx in (r'"url":"(https?:[^"\\]+m3u8[^"]*)"', r'(https?://[^"\'\s<>]+?\.m3u8[^"\'\s<>]*)'):
                mm = re.search(rx, h or "")
                if mm:
                    return {"parse": 0, "url": mm.group(1).replace("\\/", "/"),
                            "header": dict(_headers())}
            return {"parse": 0, "url": purl, "header": dict(_headers())}
        except Exception:
            print("[站群] player错误:", traceback.format_exc()[-200:])
            return {"parse": 0, "url": "", "header": dict(_headers())}

    def liveContent(self, url):
        return ""

    def localProxy(self, param=None):
        return [404, "text/plain", b"", {}]

    def destroy(self):
        pass

# ---- 站池：2026-10-02 从 14 个导航页递归发现的 110 个同族站（host|tid|站名）----
_DEF = [
    "aai.ttzy7.xyz|20|通通资源", "ajp.fjc3.makeup|20|飞机场", "ako.qzjp6.ink|20|茄子精品",
    "ctl.kgys5.monster|20|快感艺术", "dbt.rqtqsp5.lat|20|人妻偷情视频", "aoq.tmxj4.pics|20|探幽寻径",
    "cok.fnyy5.yachts|20|粉嫩影院", "ahw.khcr3.help|20|快活成人", "bwa.zyccm4.ink|20|织一场春梦",
    "csb.smxy2.best|20|湿妹学院", "dat.dbyp7.help|20|杜比影片", "bcd.mesp7.fit|20|猫耳视频",
    "bod.htsy9.casa|20|海天盛筵", "edq.fls4.homes|20|福利色", "aps.nhtv8.homes|20|内涵TV",
    "bps.qyhhs9.homes|20|奇淫合欢散", "ckb.ppg8.wiki|20|啪啪哥", "dfa.hbj6.pics|20|哈勃君",
    "djb.dyrhp3.xyz|20|第一日韩片", "dpv.uuhz4.best|20|UU黄站", "duf.zhxly8.beauty|20|中华小狼友",
    "efx.77av4.top|20|77AV", "ejl.sndy2.best|20|熟女电影", "acb.pprk3.help|20|啪啪入口",
    "akq.ysav6.mom|20|夜色AV", "bdb.mfjpz9.yachts|20|免费精品站", "beq.ycfxz7.skin|1|原创分享站",
    "bns.slszx8.motorcycles|20|少林寺在线", "boe.hsck3.help|1|黄色仓库", "bph.yszy7.ink|1|淫兽资源",
    "bpi.xlkp9.pics|20|小狼看片", "bxi.gcqsw9.wiki|1|国产情色网", "cez.zatt9.makeup|20|做爱天堂",
    "ckd.bsb7.homes|1|百色榜", "cud.xysp6.beer|20|星陨视频", "dup.rcys8.beauty|20|热草影视",
    "ebn.gblwdz3.homes|1|隔壁老王的站", "aat.mwjr6.quest|1|美味佳人", "ack.jpsp2.lol|1|简评视频",
    "ago.hwpp5.quest|20|户外啪啪", "ajz.baihuzu2.fit|20|白虎族", "aoz.crhlw7.casa|20|成人葫芦娃",
    "asp.myw2.lat|20|玛雅网", "awa.jlbpw5.best|20|加勒逼片网", "bcf.wwfs4.ink|1|万物复苏",
    "bcg.mmg3.lol|1|咪咪阁", "bdv.sydf5.makeup|20|水淫洞府", "bhb.fnxym6.beauty|20|粉嫩小洋马",
    "bjq.was7.wiki|20|微爱社", "blc.txav8.motorcycles|20|溏心AV", "bqw.xptt4.motorcycles|20|XP天堂",
    "bsz.fxys8.fit|20|风雪影视", "cbf.fdndx9.pics|20|抚动你的心", "cbm.19sty4.xyz|20|19岁童颜",
    "cit.xfsh5.help|1|性福生活", "cku.szw4.autos|1|涩之味", "cpi.gcjp5.homes|20|国产精品",
    "cqe.lgcq5.casa|1|蓝光超清", "cro.avlm5.work|20|AV联盟", "cyz.xanw2.motorcycles|1|性爱女王",
    "dcr.knjw7.best|20|靠你鸡娃", "ddg.aptv4.homes|1|爱啪TV", "ddo.rltt9.homes|20|热力天堂",
    "dmg.tjsy4.pics|1|淘精岁月", "dtj.bbsz4.fit|20|爆B色站", "dwz.xmbn5.homes|20|小妹爆奶",
    "eer.aqy7.hair|20|爱骑液", "ejo.qsq5.autos|20|千色区", "ekc.yxg4.skin|20|欲仙阁",
    "aoj.csgz2.buzz|20|春色格战", "bhp.gsdfj9.makeup|20|隔山打飞机", "bwe.kssp9.best|20|快速视频",
    "dbc.izxsp6.mom|20|爱在线视频", "ada.bqs8.casa|20|飙妻社", "aml.nxsq6.wiki|20|奶心社区",
    "ask.zxbsj7.casa|20|在线保时捷", "bqz.ylqq7.buzz|20|淫领全球", "chz.xcd3.skin|20|杏冲动",
    "cqi.cysp4.beer|20|初夜视频", "dpt.ldfn4.xyz|20|浪荡妇女", "btd.lgav5.yachts|20|利高AV",
    "cyk.tangrenfuli9.yachts|20|唐人福利", "dgq.avspdq3.quest|20|AV视频大全", "egn.xysp7.autos|20|星源视频",
    "ele.lfav6.hair|20|路飞AV", "acg.zjss8.buzz|20|最佳射手", "adg.wly4.hair|20|五凉液",
    "afx.xsqj5.lat|20|夏色奇迹", "alu.xxy9.yachts|1|性学院", "ang.pbw7.quest|1|炮兵网",
    "anq.gqaw2.autos|21|高清爱微", "aqj.xhd5.wiki|20|销魂洞", "avp.qqqabc4.buzz|1|QQ视频",
    "ayz.xysy6.ink|21|小淫深夜", "bar.yhcm6.wiki|21|樱花传媒", "bhi.10dlg2.hair|21|10点撸管",
    "bjl.syav8.best|20|色欲AV", "bjv.whxm5.pics|1|网红泄密", "brj.bszx6.skin|20|百射助穴",
    "bsy.ttw4.boats|21|兔兔网", "btx.fc2gw8.skin|20|FC2官网", "cbv.bqhx3.help|1|碧曲幻想",
    "cew.mtyx9.fit|21|马桶英雄", "cgq.avjwh4.buzz|21|av居委会", "clh.myzj5.work|21|名优之家",
    "cqz.lmlm4.wiki|21|绿帽联盟", "cun.83sp7.top|20|83视频", "ddt.avdby7.fit|20|av大本营",
    "dhl.mtcm2.ink|20|蜜桃传媒", "doi.lwp3.casa|20|老污婆",
]
