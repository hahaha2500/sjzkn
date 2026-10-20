# -*- coding: utf-8 -*-
"""
FreeOK (freeok.day) — TVBox / FongMi type=3 源（Chaquopy Python）

站点说明书（2026-10-20 实测）：
1) 站型：MacCMS v10 + mxtheme 模板；采集接口 /api.php/provide/vod 返回 "closed"（已关）→ 纯 HTML 直抓。
2) URL 规律：
   首页 /
   分类 /type/{1电影,2连续剧,3动漫,4综艺}.html
   列表 /show/{tid}-----------.html（第1页） /show/{tid}--------{pg}---.html（第2页起，12段位，页码在索引8）
   详情 /detail/{id}.html
   搜索 /search/{quote关键词}-------------.html
   标签 /label/new.html /label/hot.html
3) ★本站是"导航型"站：详情页不托管播放，只给第三方播放站外链。
   外链生态（40片抽样 66 条外链统计）：
     www.hqvod.com        38 条（主力·高清点播）
     movie.qhdaohang.cn   18 条（青禾影视）
     www.4kvm.top          9 条（4KVM）
     www.wbbb1.com         1 条（歪比巴卜）
   详情页外链区 HTML：<a href="外链" target="_blank" ...><div class="site-item-row">...
4) 集数不在 freeok 详情页，必须去外链站抓：
   hqvod      /xiangqing/{id}.html  → 线路 tab(.swiper-slide) 与 集数 box(.anthology-list-play) 一一对应
   qhdaohang  /voddetail/{id}.html  → 同款 anthology 结构（MacCMS 换皮）
   taiee      /index.php/vod/detail/id/{id}.html → /index.php/vod/play/id/{id}/sid/{s}/nid/{n}.html
   4kvm       /play/{id}            → 集数链接 href="/play/xxx"（WASM 签名取流，交给 App 嗅探）
5) 播放链（纯 Python 已跑通，hqvod/taiee/qq 系）：
   外链站播放页 player_aaaa.url（明文，encrypt=0，腾讯 v.qq.com 地址）
   → 解析站 xn--2p1a.bfapi.cyou（哈喽.bfapi.cyou）/player/?url={url}
   → POST /player/api.php {url,key,vkey,ckey}
        calc = (MD5(密文)+" P")[-22:]
        key  = b64(RC4(calc, MD5(密文+"stray")))
        vkey = b64(RC4(calc, str(int(time.time()))+MD5(calc+"stray")))
        ckey = b64(RC4(calc, MD5(PARSE_HOST+"stray")))
   → 回包 {url(密文), aes_key, aes_iv}
        k  = RC4(calc, b64(aes_key)) ; iv = RC4(calc, b64(aes_iv))
        明文 = AES-128-CBC 解密 b64(url) → 真实 m3u8
6) 风控：hqvod 播放页 403 + JS 自跳，同会话重试一次即 200（cookie 机制）。
"""
import base64
import hashlib
import json
import re
import time
import gzip
import zlib
import threading
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self):
            pass

SITE = "https://freeok.day"
PARSE_HOST = "xn--2p1a.bfapi.cyou"
PARSE_ORIGIN = "https://" + PARSE_HOST
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CLASSES = [
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "连续剧"},
    {"type_id": "3", "type_name": "动漫"},
    {"type_id": "4", "type_name": "综艺"},
]

# 外链站标识 → (显示名, 详情页路径模板, 集数解析模式)
OUT_SITES = {
    "hqvod": "高清点播",
    "qhdaohang": "青禾影视",
    "taiee": "太乙电影",
    "4kvm": "4KVM",
    "wbbb1": "歪比巴卜",
}


# ============================ 加密（纯标准库） ============================
def _rc4(key, data):
    """RC4。key 可为 str/bytes；data 为 str 时返回 str，为 bytes 时返回 bytes。"""
    if isinstance(key, str):
        key = key.encode("utf-8")
    as_bytes = isinstance(data, (bytes, bytearray))
    if isinstance(data, str):
        data = data.encode("utf-8")
    S = list(range(256))
    j = 0
    kl = len(key)
    for i in range(256):
        j = (j + S[i] + key[i % kl]) % 256
        S[i], S[j] = S[j], S[i]
    out = bytearray()
    i = j = 0
    for ch in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(S[(S[i] + S[j]) % 256] ^ ch)
    if as_bytes:
        return bytes(out)
    return out.decode("latin-1")


def _calc(cipher):
    h = hashlib.md5(cipher.encode("utf-8")).hexdigest()
    return (h + " P")[-22:]


def _calce(x):
    return hashlib.md5(x.encode("utf-8")).hexdigest()


def _enplay(calc_key, x):
    """btoa(RC4(calculate(url), x))"""
    return base64.b64encode(
        _rc4(calc_key.encode("utf-8"), x.encode("utf-8"))).decode("utf-8")


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _mk_sbox():
    inv = [0] * 256
    for i in range(1, 256):
        for j in range(1, 256):
            if _gmul(i, j) == 1:
                inv[i] = j
                break
    sbox = [0] * 256
    for i, v in enumerate(inv):
        s = v ^ ((v << 1) | (v >> 7)) ^ ((v << 2) | (v >> 6)) ^ \
            ((v << 3) | (v >> 5)) ^ ((v << 4) | (v >> 4)) ^ 0x63
        sbox[i] = s & 0xFF
    return sbox


_SBOX = _mk_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if (a & 0x80) else (a << 1)


def _aes_decrypt_block(key, block):
    # key expansion
    w = list(key)
    for i in range(4, 44):
        t = w[(i - 1) * 4:i * 4]
        if i % 4 == 0:
            t = [_SBOX[t[1]] ^ _RCON[i // 4 - 1], _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
        w += [w[(i - 4) * 4 + k] ^ t[k] for k in range(4)]

    st = list(block)
    # 初始轮密钥（第10轮）
    rk = w[40 * 4:44 * 4]
    st = [st[i] ^ rk[i] for i in range(16)]
    for rnd in range(9, 0, -1):
        # InvSubBytes
        st = [_SBOX_inv(v) for v in st]
        # InvShiftRows（行循环右移）
        st = [st[0], st[13], st[10], st[7],
              st[4], st[1], st[14], st[11],
              st[8], st[5], st[2], st[15],
              st[12], st[9], st[6], st[3]]
        # InvMixColumns
        for c in range(4):
            a = st[c * 4:c * 4 + 4]
            st[c * 4] = _gm(a[0], 14) ^ _gm(a[1], 11) ^ _gm(a[2], 13) ^ _gm(a[3], 9)
            st[c * 4 + 1] = _gm(a[0], 9) ^ _gm(a[1], 14) ^ _gm(a[2], 11) ^ _gm(a[3], 13)
            st[c * 4 + 2] = _gm(a[0], 13) ^ _gm(a[1], 9) ^ _gm(a[2], 14) ^ _gm(a[3], 11)
            st[c * 4 + 3] = _gm(a[0], 11) ^ _gm(a[1], 13) ^ _gm(a[2], 9) ^ _gm(a[3], 14)
        rk = w[rnd * 4 * 4:(rnd + 1) * 4 * 4]
        st = [st[i] ^ rk[i] for i in range(16)]
    st = [_SBOX_inv(v) for v in st]
    st = [st[0], st[13], st[10], st[7],
          st[4], st[1], st[14], st[11],
          st[8], st[5], st[2], st[15],
          st[12], st[9], st[6], st[3]]
    rk = w[0:16]
    return bytes(st[i] ^ rk[i] for i in range(16))


_SBOX_INV = None


def _SBOX_inv(v):
    global _SBOX_INV
    if _SBOX_INV is None:
        _SBOX_INV = [0] * 256
        for i, s in enumerate(_SBOX):
            _SBOX_INV[s] = i
    return _SBOX_INV[v]


def _gm(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _aes_cbc_decrypt(key, iv, data):
    """AES-128-CBC + PKCS7 去填充（优先 pycryptodome，否则纯 Python）"""
    try:
        from Crypto.Cipher import AES
        pt = AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except Exception:
        pt = bytearray()
        prev = iv
        for i in range(0, len(data), 16):
            blk = bytes(data[i + k] ^ prev[k] for k in range(16))
            dec = _aes_decrypt_block(key, blk)
            pt += bytes(dec[k] ^ prev[k] for k in range(16))
            prev = data[i:i + 16]
        pt = bytes(pt)
    pad = pt[-1]
    if 1 <= pad <= 16:
        pt = pt[:-pad]
    return pt

# ============================ 会话 ============================
class Session(object):
    def __init__(self, host=""):
        self.host = (host or SITE).rstrip("/")
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self._last = 0.0
        self.last_err = ""
        self._cache = {}

    def _throttle(self, gap=0.2):
        dt = time.time() - self._last
        if dt < gap:
            time.sleep(gap - dt)

    def _headers(self, referer=None, extra=None, dest="document"):
        h = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Sec-Fetch-Dest": dest,
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            h["Referer"] = referer
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _decompress(body, enc):
        if not body:
            return body
        try:
            if body[:2] == b"\x1f\x8b":
                return gzip.decompress(body)
            if enc == "gzip":
                return gzip.decompress(body)
            if body[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda") or enc == "deflate":
                try:
                    return zlib.decompress(body)
                except Exception:
                    return zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception:
            return body
        return body

    def raw(self, url, referer=None, data=None, extra=None,
            dest="document", timeout=12, tries=2):
        deadline = time.time() + (timeout + 8)
        for i in range(tries):
            if time.time() > deadline:
                break
            self._throttle()
            try:
                req = urllib.request.Request(
                    url, data=data, headers=self._headers(referer, extra, dest))
                with self.opener.open(req, timeout=timeout) as r:
                    raw = r.read()
                    enc = (r.headers.get("Content-Encoding") or "").lower().strip()
                    self._last = time.time()
                    return r.status, self._decompress(raw, enc)
            except urllib.error.HTTPError as e:
                body = b""
                try:
                    body = e.read()
                except Exception:
                    pass
                enc = ""
                try:
                    enc = (e.headers.get("Content-Encoding") or "").lower().strip()
                except Exception:
                    enc = ""
                self._last = time.time()
                return e.code, self._decompress(body, enc)
            except Exception as e:
                self.last_err = repr(e)[:80]
                time.sleep(1)
        return None, (self.last_err or "").encode("utf-8", "ignore")

    def get(self, url, referer=None, tries=4, timeout=12):
        """GET + 自动重试 403 自跳页（同会话 cookie 机制）
        自跳页特征：window.location.href 重定向（长度不固定，含 CF beacon 时可到 500+）
        ★hqvod 需连打 2~3 次才放行，tries 给足 5 次。"""
        st, body = self.raw(url, referer=referer, tries=1, timeout=timeout)
        text = body.decode("utf-8", "ignore") if body else ""
        for _ in range(max(1, tries - 1)):
            if st == 200 and text and "window.location.href" not in text[:800]:
                break
            if st is not None and st not in (403, 429, 503) \
                    and "window.location.href" not in text[:800]:
                break
            time.sleep(0.8)
            st, body = self.raw(url, referer=referer, tries=1, timeout=timeout)
            text = body.decode("utf-8", "ignore") if body else ""
        return st, body

    def post(self, url, data, referer=None, timeout=15):
        body = urllib.parse.urlencode(data).encode("utf-8")
        return self.raw(url, referer=referer, data=body, dest="empty",
                        extra={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=timeout, tries=2)


# ============================ freeok 解析 ============================
def _clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _abs(u, base):
    """base 可以是完整 URL 或站点根；站点根 = scheme://host"""
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        root = base
        if not root.endswith("/"):
            parts = root.split("/", 3)
            root = parts[0] + "//" + parts[2] + "/"
        return root + u.lstrip("/")
    return u


def _cards(html, base, limit=0):
    """列表卡片：module-poster-item（首页/分类）+ module-card-item（搜索）"""
    out = []
    seen = set()

    def push(vid, name, pic, note, cls):
        if not vid or vid in seen:
            return
        seen.add(vid)
        out.append({"vod_id": vid, "vod_name": name or vid,
                    "vod_pic": pic, "vod_remarks": note or ""})

    # --- module-poster-item：<a href="/detail/X.html" title="片名" ...> ---
    for m in re.finditer(
            r'<a href="(/detail/(\d+)\.html)"[^>]*title="([^"]*)"[^>]*class="([^"]*module-poster-item[^"]*)"',
            html):
        vid, name, cls = m.group(2), _clean(m.group(3)), m.group(4)
        seg = html[m.start():m.start() + 1200]
        pic = ""
        pm = re.search(r'data-original="([^"]+)"', seg)
        if pm:
            pic = pm.group(1)
        else:
            pm = re.search(r'<img[^>]+src="([^"]+)"', seg)
            if pm:
                pic = pm.group(1)
        note = ""
        nm = re.search(r'class="module-item-note">([^<]*)<', seg)
        if nm:
            note = _clean(nm.group(1))
        # 排行榜卡（top1..top10）剔除，避免污染分类
        if "top" in cls.split():
            continue
        push(vid, name, _abs(pic, base), note, cls)

    # --- module-card-item：<div class="module-card-item module-item"> ... <a href="/detail/X.html" class="module-card-item-poster"> ---
    if not out:
        for m in re.finditer(
                r'<div class="module-card-item module-item">(.*?)</div></div></div>',
                html, re.S):
            seg = m.group(1)
            vm = re.search(r'href="/detail/(\d+)\.html"', seg)
            if not vm:
                continue
            vid = vm.group(1)
            name = ""
            tm = re.search(r'class="module-card-item-title">.*?<strong>([^<]*)</strong>', seg, re.S)
            if tm:
                name = _clean(tm.group(1))
            pic = ""
            pm = re.search(r'data-original="([^"]+)"', seg)
            if pm:
                pic = pm.group(1)
            note = ""
            cm = re.search(r'class="module-card-item-class">([^<]*)<', seg)
            if cm:
                note = _clean(cm.group(1))
            im = re.search(r'class="module-info-item-content">([^<]*)<', seg)
            if im:
                note = (note + " " + _clean(im.group(1))).strip()
            push(vid, name, _abs(pic, base), note, "")
    if limit:
        out = out[:limit]
    return out


def _page_count(html, tid, pg):
    m = re.search(r'href="/show/[\d-]*?(\d+)---\.html"[^>]*title="尾页"', html)
    if m:
        try:
            return max(int(m.group(1)), pg)
        except Exception:
            return pg
    m = re.search(r'title="尾页"[^>]*href="/show/[\d-]*?(\d+)---\.html"', html)
    if m:
        try:
            return max(int(m.group(1)), pg)
        except Exception:
            return pg
    return pg + 1


def _detail_info(html):
    info = {"name": "", "pic": "", "desc": "", "tags": []}
    m = re.search(r'<h1>([^<]*)</h1>', html)
    if m:
        info["name"] = _clean(m.group(1))
    if not info["name"]:
        m = re.search(r'<title>《([^》]*)》', html)
        if m:
            info["name"] = _clean(m.group(1))
    m = re.search(r'class="module-info-poster".*?data-original="([^"]+)"', html, re.S)
    if m:
        info["pic"] = m.group(1)
    else:
        m = re.search(r'class="module-info-poster".*?<img[^>]+src="([^"]+)"', html, re.S)
        if m:
            info["pic"] = m.group(1)
    m = re.search(r'class="module-info-introduction-content[^"]*"[^>]*>(.*?)</div>', html, re.S)
    if m:
        info["desc"] = _clean(m.group(1))[:1200]
    for mm in re.finditer(r'<span class="module-info-item-title">([^：:]+)[：:]</span>'
                          r'<div class="module-info-item-content">(.*?)</div>', html, re.S):
        tag = _clean(mm.group(1))
        val = _clean(mm.group(2))
        if not val:
            continue
        if tag in ("主演", "导演"):
            info["tags"].append(tag + ":" + val[:60])
        elif tag in ("更新", "上映日期", "状态"):
            info["tags"].append(tag + ":" + val[:30])
    return info


def _outlinks(html):
    """详情页外链站：[(显示名, url)]"""
    res = []
    seen = set()
    i = html.find("site-list-vertical")
    if i < 0:
        i = html.find("play_source")
    seg = html[i:i + 20000] if i >= 0 else html
    for m in re.finditer(
            r'<a href="(https?://[^"]+)"[^>]*>\s*<div class="site-item-row"(.*?)</a>',
            seg, re.S):
        url = m.group(1)
        if url in seen:
            continue
        seen.add(url)
        name = ""
        nm = re.search(r'<span style="flex-shrink: 0;">([^<]*)</span>', m.group(2))
        if nm:
            name = _clean(nm.group(1))
        res.append((name, url))
    if not res:
        for m in re.finditer(r'href="(https?://[^"]+)" target="_blank"[^>]*>'
                             r'style="text-decoration: none[^"]*"[^>]*>(.*?)</a>', seg, re.S):
            url = m.group(1)
            if url in seen:
                continue
            seen.add(url)
            nm = re.search(r'website-name.*?<span style="flex-shrink: 0;">([^<]*)</span>',
                           m.group(2), re.S)
            res.append((_clean(nm.group(1)) if nm else "", url))
    return res


def _site_key(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    for k in OUT_SITES:
        if k in h:
            return k
    if "4kvm" in h:
        return "4kvm"
    if "taiee" in h:
        return "taiee"
    return ""


# ============================ 外链站集数 ============================
def _anthology(html, play_pat, base):
    """hqvod / qhdaohang 同款 anthology 结构：线路 tab 与集数 box 一一对应"""
    # 线路名：<a class="swiper-slide"><i ...></i>&nbsp;名称<span class="badge">N</span></a>
    tabs = re.findall(r'class="swiper-slide"><i class="fa ds-dianying"></i>&nbsp;'
                      r'([^<]*?)(?:<span class="badge">(\d+)</span>)?</a>', html)
    boxes = re.findall(r'<ul class="anthology-list-play size">(.*?)</ul>', html, re.S)
    if not tabs:
        tabs = re.findall(r'class="swiper-slide"[^>]*>(?:<i[^>]*></i>&nbsp;)?'
                          r'([^<]{1,30})</a>', html)
    lines = []
    for i, tp in enumerate(tabs):
        if i >= len(boxes):
            break
        eps = re.findall(r'href="(' + play_pat + r')"[^>]*>([^<]*)</a>', boxes[i])
        if not eps:
            continue
        tname = _clean(tp[0] if isinstance(tp, tuple) else tp) or ("线路%d" % (i + 1))
        segs = []
        for u, nm in eps:
            nm = _clean(nm)
            segs.append("%s$%s" % (nm or "正片", _abs(u, base)))
        lines.append((tname, segs))
    return lines


def _out_lines(sess, key, url):
    """去外链站抓线路+集数。返回 [(线路名, [seg...])]"""
    try:
        st, body = sess.get(url, referer=SITE + "/", tries=3)
        html = body.decode("utf-8", "ignore") if body else ""
    except Exception:
        return []
    if not html or len(html) < 1500:
        return []
    try:
        if key == "hqvod":
            lines = _anthology(html, r"/bofang/[\w-]+\.html", url)
            if lines:
                return lines
        if key in ("qhdaohang", "taiee"):
            if key == "qhdaohang":
                lines = _anthology(html, r"/vodplay/[\w-]+\.html", url)
            else:
                lines = _anthology(html, r"/index\.php/vod/play/id/[\d/]+.html", url)
            if lines:
                return lines
            # 退化：单线路全量
            pat = (r"/vodplay/[\w-]+\.html" if key == "qhdaohang"
                   else r"/index\.php/vod/play/id/[\d/]+.html")
            eps = re.findall(r'href="(' + pat + r')"[^>]*>([^<]*)</a>', html)
            if eps:
                segs = ["%s$%s" % (_clean(nm) or "正片", _abs(u, url)) for u, nm in eps]
                return [("主线", segs)]
        if key == "4kvm":
            eps = re.findall(r'href="(/play/[^"]+)"', html)
            if eps:
                segs = []
                for i, u in enumerate(eps):
                    segs.append("第%d集$%s" % (i + 1, _abs(u, url)))
                return [("4KVM", segs)]
    except Exception:
        return []
    return []


def _out_lines_multi(sess, outs, max_sites=3, max_lines=6):
    """并行抓多个外链站，合并线路。限站点数与线路数，避免详情接口超时。"""
    picked = []
    # 优先主力站
    prio = ["hqvod", "qhdaohang", "4kvm", "taiee"]
    keyed = []
    for name, url in outs:
        k = _site_key(url)
        if k:
            keyed.append((k, name, url))
    keyed.sort(key=lambda x: prio.index(x[0]) if x[0] in prio else 99)
    keyed = keyed[:max_sites]
    if not keyed:
        return []

    results = {}
    lock = threading.Lock()

    def work(item):
        k, name, url = item
        try:
            lines = _out_lines(sess, k, url)
        except Exception:
            lines = []
        with lock:
            results[k] = (name, url, lines)

    threads = []
    for item in keyed:
        t = threading.Thread(target=work, args=(item,))
        t.daemon = True
        t.start()
        threads.append(t)
    for t in threads:
        t.join(45)

    # 第一轮：每站各取 1 条（保证多站覆盖，任一站挂了还有别的）
    out = []
    seen_tags = set()
    deferred = []
    for k, name, url in keyed:
        r = results.get(k)
        if not r:
            continue
        _, _, lines = r
        got_one = False
        disp = (name or OUT_SITES.get(k, "")) or OUT_SITES.get(k, "线路")
        for lname, segs in lines:
            if not segs:
                continue
            tag = "%s·%s" % (disp, lname) if lname else disp
            if tag in seen_tags:
                continue
            seen_tags.add(tag)
            if not got_one:
                out.append(("FreeOK·%s" % tag, segs))
                got_one = True
            else:
                deferred.append(("FreeOK·%s" % tag, segs))
    # 第二轮：补足到 max_lines
    for item in deferred:
        if len(out) >= max_lines:
            break
        out.append(item)
    return out


# ============================ 播放解析 ============================
def resolve_m3u8(sess, cipher):
    """解析站解密：v.qq.com 等明文地址 → 真实 m3u8"""
    page = PARSE_ORIGIN + "/player/?url=" + urllib.parse.quote(cipher)
    try:
        sess.raw(page, referer=SITE + "/", dest="document", timeout=18, tries=1)
    except Exception:
        pass
    c = _calc(cipher)
    t = int(time.time())
    kv = _enplay(c, _calce(cipher + "stray"))
    vv = _enplay(c, str(t) + _calce(c + "stray"))
    cv = _enplay(c, _calce(PARSE_HOST + "stray"))
    try:
        st, body = sess.post(PARSE_ORIGIN + "/player/api.php",
                             {"url": cipher, "key": kv, "vkey": vv, "ckey": cv},
                             referer=page)
    except Exception:
        return ""
    if not body:
        return ""
    try:
        j = json.loads(body.decode("utf-8", "ignore"))
    except Exception:
        return ""
    if not isinstance(j, dict) or j.get("code") != 200 or not j.get("url"):
        return ""
    try:
        k = _rc4(c.encode("utf-8"), base64.b64decode(j["aes_key"]))
        iv = _rc4(c.encode("utf-8"), base64.b64decode(j["aes_iv"]))
        plain = _aes_cbc_decrypt(k, iv, base64.b64decode(j["url"]))
        return plain.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _play_page_url(sess, url, referer, _depth=0):
    """取外链站播放页里的 player_aaaa.url（明文）
    若该 url 本身是"分享页"（内部再嵌一层 m3u8），递归解一层。"""
    st, body = sess.get(url, referer=referer, tries=1)
    html = body.decode("utf-8", "ignore") if body else ""
    # 风控/自跳页 → 再打一轮（hqvod 常需 2~3 次）
    if not html or "window.location.href" in html[:800] or len(html) < 2000:
        st, body = sess.get(url, referer=referer, tries=3)
        html = body.decode("utf-8", "ignore") if body else ""
    if not html:
        return ""
    m = re.search(r'var player_aaaa\s*=\s*(\{.*?\})\s*</script>', html, re.S)
    if m:
        blob = m.group(1)
        um = re.search(r'"url"\s*:\s*"([^"]+)"', blob)
        if um:
            inner = um.group(1).replace("\\/", "/")
            # 分享页（/share/xxx）：再进去一层取真实 m3u8
            if "/share/" in inner and _depth < 2:
                base = inner
                mm = re.match(r"(https?://[^/]+)", base)
                sub = _play_page_url(sess, base, (mm.group(1) + "/") if mm else referer,
                                     _depth + 1)
                if sub:
                    return sub
            return inner
    um = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', html)
    if um:
        return um.group(1).replace("\\/", "/")
    # qhdaohang 系 share 页：const url = "/20250xxx/xxx/index.m3u8?sign=..."
    sm = re.search(r'const url\s*=\s*"([^"]+\.m3u8[^"]*)"', html)
    if sm:
        u = sm.group(1)
        if u.startswith("/"):
            mm = re.match(r"(https?://[^/]+)", url)
            if mm:
                u = mm.group(1) + u
        return u
    um = re.search(r'https?://[^"\'\s]+\.m3u8[^"\'\s]*', html)
    if um:
        return um.group(0)
    return ""


# ============================ 主体 ============================
class Spider(BaseSpider):
    def __init__(self):
        try:
            super(Spider, self).__init__()
        except TypeError:
            pass
        self.extend = ""
        self.sess = None
        self.home_limit = 48
        self._ext_cache = {}

    # ---------- 加载层（webhtv 契约） ----------
    def init(self, extend=""):
        self.extend = extend or ""
        host = SITE
        cfg = {}
        if isinstance(extend, dict):
            cfg = extend
        elif isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                cfg = json.loads(extend)
            except Exception:
                cfg = {}
        if isinstance(cfg, dict):
            if cfg.get("host"):
                host = str(cfg["host"]).strip()
                if not host.startswith("http"):
                    host = "https://" + host
            try:
                if cfg.get("home") is not None:
                    self.home_limit = max(0, int(cfg.get("home")))
            except Exception:
                pass
        self.sess = Session(host)
        return self

    def getDependence(self):
        return []

    def getName(self):
        return "FreeOK追剧"

    def isVideoFormat(self, url):
        return any(e in str(url).lower()
                   for e in [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    def _s(self):
        if self.sess is None:
            self.init(self.extend)
        return self.sess

    # ---------- 首页 ----------
    def homeContent(self, filter=False):
        s = self._s()
        html = ""
        try:
            st, body = s.get(s.host + "/", tries=3)
            html = body.decode("utf-8", "replace") if body else ""
        except Exception as e:
            s.last_err = repr(e)[:60]
        lst = _cards(html, s.host, self.home_limit) if html else []
        if not lst:
            lst = [{"vod_id": "0", "vod_name": "[诊断] 首页抓取失败",
                    "vod_pic": "", "vod_remarks": "反馈这行给我",
                    "vod_content": "原因: %s ｜ 时间: %s" % (
                        s.last_err or "未知", time.strftime("%H:%M:%S"))}]
        return {"class": CLASSES, "list": lst, "filters": self._filters()}

    def homeVideoContent(self):
        s = self._s()
        html = ""
        try:
            st, body = s.get(s.host + "/", tries=2)
            html = body.decode("utf-8", "replace") if body else ""
        except Exception:
            html = ""
        return {"list": _cards(html, s.host, self.home_limit) if html else []}

    def _filters(self):
        return {c["type_id"]: [
            {"key": "sort", "name": "排序",
             "value": [{"n": "最新", "v": "newest"}, {"n": "最热", "v": "hits"}]},
        ] for c in CLASSES}

    # ---------- 分类 ----------
    def categoryContent(self, tid, pg, filter, extend):
        s = self._s()
        tid = str(tid or "1")
        if not tid.isdigit():
            tid = "1"
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        if pg <= 1:
            url = "%s/show/%s-----------.html" % (s.host, tid)
        else:
            url = "%s/show/%s--------%d---.html" % (s.host, tid, pg)
        st, body = s.get(url, referer="%s/type/%s.html" % (s.host, tid), tries=3)
        html = body.decode("utf-8", "replace") if body else ""
        items = _cards(html, s.host)
        pages = _page_count(html, tid, pg)
        if not items:
            pages = pg                      # 空页刹车
        pages = max(pages, pg)
        return {"page": pg, "pagecount": pages, "limit": 72,
                "total": pages * 72, "list": items}

    # ---------- 搜索 ----------
    def searchContent(self, key, quick, pg="1"):
        s = self._s()
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        kw = urllib.parse.quote(str(key or "")) + "-------------"
        if pg > 1:
            kw = urllib.parse.quote(str(key or "")) + "----------%d---" % pg
        url = "%s/search/%s.html" % (s.host, kw)
        st, body = s.get(url, referer=s.host + "/", tries=3)
        html = body.decode("utf-8", "replace") if body else ""
        items = _cards(html, s.host)
        pages = _page_count(html, "", pg)
        if not items:
            pages = pg
        pages = max(pages, pg)
        return {"page": pg, "pagecount": pages, "limit": 72,
                "total": pages * 72, "list": items}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        s = self._s()
        vid = str(ids[0]) if ids else ""
        vid = vid.split("@@")[-1].strip()
        if not vid.isdigit():
            return {"list": []}
        url = "%s/detail/%s.html" % (s.host, vid)
        st, body = s.get(url, referer=s.host + "/", tries=2)
        html = body.decode("utf-8", "replace") if body else ""
        if not html or len(html) < 2000:
            return {"list": []}
        info = _detail_info(html)
        outs = _outlinks(html)

        # 并行抓外链站集数（限 3 站 / 6 线路，避免超时）
        pairs = _out_lines_multi(s, outs, max_sites=4, max_lines=8)
        play_from = [p[0] for p in pairs]
        play_url = ["#".join(p[1]) for p in pairs]
        if not play_url:
            # 兜底：所有外链都给一条原页线
            for name, ourl in outs[:4]:
                play_from.append("FreeOK·%s" % (name or "原页"))
                play_url.append("在线观看$" + ourl)
        if not play_url:
            play_from = ["FreeOK·原页"]
            play_url = ["在线观看$" + url]

        remark = " / ".join(info.get("tags", [])[:3])
        return {"list": [{
            "vod_id": vid,
            "vod_name": info.get("name") or ("影片%s" % vid),
            "vod_pic": info.get("pic", ""),
            "vod_remarks": remark,
            "vod_content": info.get("desc", ""),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }]}

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags):
        s = self._s()
        url = str(id or "")
        if not url:
            return {"parse": 0, "url": "", "header": {}}
        flag = str(flag or "")
        if not url.startswith("http"):
            url = urllib.parse.urljoin(SITE + "/", url)

        # 嗅探线：直接交 App 内建嗅探
        if "嗅探" in flag:
            return {"parse": 0, "url": url, "header": {
                "User-Agent": UA, "Referer": SITE + "/"}}
        if "解析" in flag:
            return {"parse": 1, "url": url, "header": {"User-Agent": UA}}

        # 直连线：取外链播放页 → player_aaaa.url → 解析站解密
        try:
            referer = url
            m = re.match(r"(https?://[^/]+)", url)
            if m:
                referer = m.group(1) + "/"
            cipher = _play_page_url(s, url, referer)
            if cipher:
                if ".m3u8" in cipher or ".mp4" in cipher:
                    return {"parse": 0, "url": cipher, "header": {
                        "User-Agent": UA, "Referer": referer}}
                m3u8 = resolve_m3u8(s, cipher)
                if m3u8:
                    return {"parse": 0, "url": m3u8, "header": {
                        "User-Agent": UA, "Referer": PARSE_ORIGIN + "/"}}
        except Exception as e:
            s.last_err = "play:%s" % repr(e)[:60]

        # 回落：原页交 App 嗅探
        return {"parse": 0, "url": url, "header": {
            "User-Agent": UA, "Referer": referer,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}}

    def localProxy(self, param=None):
        return [200, "application/json; charset=utf-8",
                json.dumps({"code": 1, "msg": "freeok"}).encode("utf-8"), {}]

    def action(self, action):
        try:
            return {"action": action}
        except Exception:
            return {}

    def liveContent(self, url):
        try:
            return {"list": []}
        except Exception:
            return {"list": []}
