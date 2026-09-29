# -*- coding: utf-8 -*-
"""
歪比巴卜 wbbb1.com — TVBox / FongMi type=3 源（Chaquopy Python）

站点说明书（2026-09-29 实测）：
1) 站型：MacCMS v10 + mxtheme 模板，Cloudflare，HTTPS-only（HTTP 301→HTTPS）。
2) 首页 / 服务端直出；分类 /type/{1电影,2电视剧,3综艺,4动漫}.html；
   列表 /show/{tid}-----------.html（第1页） /show/{tid}--------{pg}---.html（第2页起）；
   详情 /detail/{id}.html；播放 /vplay/{id}-{sid}-{nid}.html；搜索 /search/{kw}-------------.html
3) 三道风控（全部已逆向，Python 自动过）：
   a) 详情/播放页「滑动验证」403：页面带 /huadong_*.js，key/value 明文写在里面，
      GET 验证端点(带 key + md5(逐字符+1拼接))即放行并给 cookie。
   b) 搜索页「系统安全验证」：图形验证码(128x40 PNG) → ddddocr 识别 → POST verify_check → 放行。
   c) 详情页偶发 JS 自跳转（window.location.href 自跳 + cookie），跟随即可。
4) 播放链（已 100% 移植 Python，纯标准库）：
   播放页 player_aaaa.url（密文）→ 解析站 哈喽.850088.xyz /player/?url=密文
   → POST /player/api.php {url,key,vkey,ckey}（三个 key = RC4(calc(密文), MD5(...))）
   → 回包 {url, aes_key, aes_iv}
   → calc=(MD5(密文)+" P")[-22:]；key=RC4(calc, b64(aes_key))；iv=RC4(calc, b64(aes_iv))
   → AES-CBC 解密 url → 真实 m3u8（带时效签名 t/tk）→ parse=0 直接播
"""
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import threading
import http.cookiejar
from html import unescape

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self, extend=""):
            pass

SITE = "https://wbbb1.com"
PARSE_HOST = "xn--qvr2v.850088.xyz"          # 哈喽.850088.xyz（解析站）
PARSE_ORIGIN = "https://" + PARSE_HOST
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CLASSES = [
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "电视剧"},
    {"type_id": "3", "type_name": "综艺"},
    {"type_id": "4", "type_name": "动漫"},
]


# ============================ 加密（纯标准库） ============================
def _rc4(key, data):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for b in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def _calc(cipher):
    """calculate(x) = (MD5(x).hex + ' P')[-22:]"""
    return (hashlib.md5(cipher.encode("utf-8")).hexdigest() + " P")[-22:]


def _calce(x):
    """calculatee(x) = MD5(x).hex"""
    return hashlib.md5(x.encode("utf-8")).hexdigest()


def _enplay(calc_key, x):
    """enplay(x) = btoa(RC4(calculate(url), x))"""
    return base64.b64encode(_rc4(calc_key.encode("utf-8"), x.encode("utf-8"))).decode("utf-8")


# ---- AES-128-CBC 解密（SBOX 运行时生成，无第三方依赖） ----
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
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self._last = 0.0
        self.last_err = ""
        self._ocr = None
        self._cache = {}

    def _throttle(self, gap=0.25):
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
            "DNT": "1",
        }
        if referer:
            h["Referer"] = referer
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _decompress(body, enc):
        """按魔数嗅探解压（Content-Encoding 头可能缺失/不准，直接认 gzip 魔数最稳）"""
        if not body:
            return body
        try:
            if body[:2] == b"\x1f\x8b":
                import gzip
                return gzip.decompress(body)
            if enc == "gzip":
                import gzip
                return gzip.decompress(body)
            if body[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda") or enc == "deflate":
                import zlib
                try:
                    return zlib.decompress(body)
                except Exception:
                    return zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception:
            return body
        return body

    def raw(self, url, referer=None, data=None, extra=None, dest="document", timeout=25):
        # 站点硬限制：任何打到 /search/ 的请求间隔 >= 3 秒（验证码链内部也算）
        if "/search/" in url:
            gap = time.time() - getattr(self, "_last_search_req", 0)
            if gap < 4.0:                  # 站点 3 秒限流，留 1 秒余量
                time.sleep(4.0 - gap)
            self._last_search_req = time.time()
        deadline = time.time() + (timeout + 8)
        for i in range(2 if timeout <= 25 else 3):
            if time.time() > deadline:
                break
            self._throttle()
            try:
                req = urllib.request.Request(url, data=data,
                                             headers=self._headers(referer, extra, dest))
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
                return e.code, self._decompress(body, enc)   # 403 的滑块页也是 gzip，必须解压
            except Exception as e:
                self.last_err = repr(e)[:80]
                time.sleep(3)
        return None, (self.last_err or "").encode("utf-8", "ignore")

    # ---------- 风控 1：滑动验证 ----------
    def _slide_pass(self, url, referer):
        """检测滑块页 → 取 JS → 算 key/value → 打验证端点 → 拿 cookie"""
        st, body = self.raw(url, referer=referer)
        if not body or "滑动验证" not in body[:4000].decode("utf-8", "ignore"):
            return st, body
        html = body.decode("utf-8", "ignore")
        m = re.search(r'src="(/huadong_[^"]+\.js[^"]*)"', html)
        if not m:
            return st, body
        js_url = self.host + m.group(1)
        st2, js = self.raw(js_url, referer=url, dest="script")
        jt = (js or b"").decode("utf-8", "ignore")
        km = re.search(r'key="([0-9a-f]+)"\s*,\s*value="([0-9a-f]+)"', jt)
        pm = re.search(r'c\.get\("(/[^"]+?\.php\?type=[^"]+)"', jt)
        if not km or not pm:
            return st, body
        key, value = km.group(1), km.group(2)
        # JS: value 每个字符 charCode+1 拼接，再取 MD5
        payload = "".join(str(ord(c) + 1) for c in value)
        md5 = hashlib.md5(payload.encode("utf-8")).hexdigest()
        ep = pm.group(1)
        verify = (self.host + ep + key + "&value=" + md5) if ep.endswith("key=") \
            else (self.host + ep + "&key=" + key + "&value=" + md5)
        self.raw(verify, referer=url, dest="empty")
        time.sleep(0.6)
        return self.raw(url, referer=referer)

    # ---------- 风控 2：搜索图形验证码 ----------
    def _search_pass(self, url, referer):
        def ok_page(st, body):
            txt = body[:4000].decode("utf-8", "ignore") if body else ""
            if body and "系统安全验证" not in txt and "频繁操作" not in txt:
                return True
            return False

        st, body = self.raw(url, referer=referer)
        # 已过验证但撞限流 → 等待重试
        if getattr(self, "_search_ok", False):
            for _ in range(3):
                if ok_page(st, body):
                    return st, body
                time.sleep(5)
                st, body = self.raw(url, referer=referer)
            return st, body
        # 还没过验证：先把验证码页拿稳（可能先撞一次限流）
        for _ in range(3):
            if body and "系统安全验证" in body[:4000].decode("utf-8", "ignore"):
                break
            time.sleep(5)
            st, body = self.raw(url, referer=referer)
        if not body or "系统安全验证" not in body[:4000].decode("utf-8", "ignore"):
            return st, body
        for attempt in range(5):
            st2, img = self.raw(self.host + "/index.php/verify/index.html",
                                referer=referer, dest="image")
            if not img:
                break
            code = self._ocr_recognize(img)
            if not code:
                continue
            data = urllib.parse.urlencode({}).encode("utf-8")
            st3, out = self.raw(
                self.host + "/index.php/ajax/verify_check?type=search&verify=" +
                urllib.parse.quote(code), referer=referer, data=data,
                extra={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                       "X-Requested-With": "XMLHttpRequest"}, dest="empty")
            try:
                j = json.loads((out or b"").decode("utf-8", "ignore"))
            except Exception:
                j = {}
            if j.get("code") == 1:
                self._search_ok = True
                time.sleep(4.5)             # 给站点 3 秒搜索间隔留够
                st, body = self.raw(url, referer=referer)
                if ok_page(st, body):
                    return st, body
                time.sleep(5)
                return self.raw(url, referer=referer)
            time.sleep(0.8)
        return st, body

    def warm(self):
        """后台预热 OCR 模型（首页加载时就跑，省掉首次搜索的等待）"""
        if self._ocr is not None or getattr(self, "_warm_started", False):
            return
        self._warm_started = True

        def run():
            try:
                import ddddocr
                self._ocr = ddddocr.DdddOcr(show_ad=False)
            except Exception:
                self._ocr = False
        threading.Thread(target=run, daemon=True).start()

    def _ocr_recognize(self, img):
        if self._ocr is None:
            self.warm()
            for _ in range(40):          # 等预热线程就绪（最多 20 秒）
                if self._ocr is not None:
                    break
                time.sleep(0.5)
        if self._ocr is None:
            try:
                import ddddocr
                self._ocr = ddddocr.DdddOcr(show_ad=False)
            except Exception:
                self._ocr = False
        if not self._ocr:
            return ""
        try:
            code = self._ocr.classification(img) or ""
        except Exception:
            return ""
        # 站点验证码固定 4 位数字；出字母/位数不对 → 当作失败，重取一张
        return code if (len(code) == 4 and code.isdigit()) else ""

    def get(self, url, referer=None, allow_slide=True, allow_captcha=True, tries=3):
        """GET，按页面类型自动过对应风控"""
        last = (None, b"")
        tries = max(tries, 3)
        for i in range(tries):
            if allow_slide:
                st, body = self._slide_pass(url, referer)
            else:
                st, body = self.raw(url, referer=referer)
            txt = body[:4000].decode("utf-8", "ignore") if body else ""
            if allow_captcha and "系统安全验证" in txt:
                st, body = self._search_pass(url, referer)
                txt = body[:4000].decode("utf-8", "ignore") if body else ""
            # 搜索频率限制（3 秒间隔）→ 退避后重试
            if "频繁操作" in txt or "间隔" in txt:
                last = (st, body)
                time.sleep(4.5)
                continue
            # 403 + JS 自跳（382B 短页，带 cookie 后重试即 200）→ 当临时挑战
            if body and (len(body) < 1500 and ("window.location.href" in txt or st == 403)):
                last = (st, body)
                time.sleep(2.0)
                continue
            if "滑动验证" not in txt and "系统安全验证" not in txt and body:
                return st, body
            last = (st, body)
            time.sleep(1.5)
        return last

    def post_api(self, url, data, referer, dest="empty"):
        payload = urllib.parse.urlencode(data).encode("utf-8")
        return self.raw(url, referer=referer, data=payload,
                        extra={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                               "X-Requested-With": "XMLHttpRequest",
                               "Origin": PARSE_ORIGIN if PARSE_HOST in url else self.host},
                        dest=dest, timeout=20)


# ============================ 播放解析 ============================
def resolve_m3u8(sess, cipher):
    """播放密文 → 解析站 api.php → RC4/AES → 真实 m3u8"""
    page = PARSE_ORIGIN + "/player/?url=" + urllib.parse.quote(cipher)
    # 解析站无风控，GET 一次建立会话即可
    sess.raw(page, referer=SITE + "/", dest="document", timeout=20)
    c = _calc(cipher)
    t = int(time.time())
    kv = _enplay(c, _calce(cipher + "stray"))
    vv = _enplay(c, str(t) + _calce(c + "stray"))
    cv = _enplay(c, _calce(PARSE_HOST + "stray"))
    st, body = sess.post_api(
        PARSE_ORIGIN + "/player/api.php",
        {"url": cipher, "key": kv, "vkey": vv, "ckey": cv}, referer=page)
    if not body:
        return ""
    try:
        j = json.loads(body.decode("utf-8", "ignore"))
    except Exception:
        return ""
    if j.get("code") != 200 or not j.get("url"):
        return ""
    try:
        k = _rc4(c.encode("utf-8"), base64.b64decode(j["aes_key"]))
        iv = _rc4(c.encode("utf-8"), base64.b64decode(j["aes_iv"]))
        plain = _aes_cbc_decrypt(k, iv, base64.b64decode(j["url"]))
        return plain.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


# ============================ HTML 解析 ============================
def _clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _home_cards(html, limit=0):
    """首页/分类（module-poster-item，title 在 a 上）与搜索（module-card-item，
    标题在 <strong> 里）两种卡片结构都兼容"""
    out, seen = [], set()

    def push(did, title, note, pic):
        title = _clean(title)
        if not did or not title or did in seen:
            return
        seen.add(did)
        out.append({"vod_id": did, "vod_name": title, "vod_pic": pic or "",
                    "vod_remarks": _clean(note)[:40]})
        if limit and len(out) >= limit:
            raise StopIteration

    try:
        # 1) 按卡片块切分
        blocks = re.split(r'class="module-(?:poster|card)-item module-item"', html)
        for blk in blocks[1:]:
            m = re.search(r'href="/detail/(\d+)\.html"', blk)
            if not m:
                continue
            did = m.group(1)
            t = re.search(r'title="([^"]+)"', blk) or \
                re.search(r'<strong>([^<]+)</strong>', blk) or \
                re.search(r'alt="([^"]+)"', blk)
            title = t.group(1) if t else ""
            n = re.search(r'class="module-item-note"[^>]*>([^<]*)<', blk)
            note = n.group(1) if n else ""
            if not note:
                n2 = re.search(r'class="module-info-item-content">\s*([^<]+)', blk)
                note = n2.group(1) if n2 else ""
            c = re.search(r'data-original="([^"]+)"', blk)
            pic = c.group(1) if c else ""
            push(did, title, note, pic)

        # 2) 兜底：按链接扫（无卡片块时）
        if not out:
            for m in re.finditer(r'<a\s+href="/detail/(\d+)\.html"([^>]*)>(.*?)</a>',
                                 html, re.S):
                did, attrs, body = m.group(1), m.group(2), m.group(3)
                t = re.search(r'title="([^"]+)"', attrs) or \
                    re.search(r'<strong>([^<]+)</strong>', body) or \
                    re.search(r'alt="([^"]+)"', body)
                title = t.group(1) if t else ""
                n = re.search(r'class="module-item-note"[^>]*>([^<]*)<', body)
                c = re.search(r'data-original="([^"]+)"', body)
                push(did, title, (n.group(1) if n else ""),
                     c.group(1) if c else "")
    except StopIteration:
        pass
    return out


def _page_count(html, tid, pg):
    """总页数：分页里 title="尾页" 的 href 带总页码（如 /show/1--------780---.html）"""
    for pat in (r'href="[^"]*?(\d+)-{2,4}\.html"[^>]*title="尾页"',
                r'href="[^"]*?(\d+)-{2,4}\.html"[^>]*aria-label="尾页"'):
        m = re.search(pat, html)
        if m:
            n = int(m.group(1))
            if 0 < n < 100000:
                return n
    return pg


def _detail_info(html):
    info = {}
    m = re.search(r"<h1>([^<]*)</h1>", html)
    info["name"] = _clean(m.group(1)) if m else ""
    m = re.search(r'<div class="module-info-poster".*?data-original="([^"]+)"', html, re.S)
    info["pic"] = m.group(1) if m else ""
    if not info["pic"]:
        m = re.search(r'data-original="([^"]+)"', html)
        info["pic"] = m.group(1) if m else ""
    m = re.search(r'class="module-info-introduction-content[^"]*"[^>]*>\s*<p>(.*?)</p>',
                  html, re.S)
    info["desc"] = _clean(m.group(1)) if m else ""
    tags = []
    for m in re.finditer(r'<div class="module-info-tag-link">\s*<a[^>]*>([^<]+)</a>', html):
        t = _clean(m.group(1))
        if t and t not in tags:
            tags.append(t)
    info["tags"] = tags[:6]
    return info


def _lines(html):
    """播放源 tab：data-dropdown-value='高清C' 等（顺序即 sid 1..N）"""
    return [unescape(x) for x in re.findall(r'data-dropdown-value="([^"]+)"', html)]


def _episodes(html, vid):
    """集数：/vplay/{vid}-{sid}-{nid}.html，按线路分组"""
    groups = {}
    for m in re.finditer(r'href="/vplay/(\d+)-(\d+)-(\d+)\.html"[^>]*title="([^"]*)"', html):
        _vid, sid, nid, title = m.group(1), int(m.group(2)), int(m.group(3)), _clean(m.group(4))
        if _vid != str(vid):
            continue
        t = re.sub(r"^播放", "", title) or ("第%s集" % nid)
        t = t.replace("$", "").replace("#", "").strip()
        groups.setdefault(sid, {})[nid] = t
    out = []
    for sid in sorted(groups):
        eps = groups[sid]
        out.append((sid, [(n, eps[n]) for n in sorted(eps)]))
    return out


# ============================ 主体 ============================
class Spider(BaseSpider):
    def __init__(self, extend=""):
        try:
            super(Spider, self).__init__()
        except TypeError:
            try:
                super(Spider, self).__init__(extend)
            except Exception:
                pass
        self.extend = extend
        self.sess = None
        self.home_limit = 40

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
        return "歪比巴卜"

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
        try:
            s.warm()                       # 后台预热 OCR
        except Exception:
            pass
        try:
            st, body = s.get(s.host + "/", tries=2)
            html = body.decode("utf-8", "replace") if body else ""
        except Exception:
            html = ""
        lst = _home_cards(html, self.home_limit) if html else []
        if not lst:
            lst = [{"vod_id": "0", "vod_name": "[诊断] 首页抓取失败",
                    "vod_pic": "", "vod_remarks": "反馈这行给我",
                    "vod_content": "原因: %s ｜ 时间: %s" % (s.last_err or "未知",
                                                        time.strftime("%H:%M:%S"))}]
        return {"class": CLASSES, "list": lst, "filters": self._filters()}

    def homeVideoContent(self):
        s = self._s()
        try:
            st, body = s.get(s.host + "/", tries=2)
            html = body.decode("utf-8", "replace") if body else ""
        except Exception:
            html = ""
        return {"list": _home_cards(html, self.home_limit) if html else []}

    def _filters(self):
        return {c["type_id"]: [
            {"key": "sort", "name": "排序",
             "value": [{"n": "最新", "v": "newest"}, {"n": "评分", "v": "grade"}]},
        ] for c in CLASSES}

    # ---------- 分类 ----------
    def categoryContent(self, tid, pg, filter, extend):
        s = self._s()
        tid = str(tid or "1")
        pg = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        if pg <= 1:
            url = "%s/show/%s-----------.html" % (s.host, tid)
        else:
            url = "%s/show/%s--------%d---.html" % (s.host, tid, pg)
        st, body = s.get(url, referer=s.host + "/type/%s.html" % tid)
        html = body.decode("utf-8", "replace") if body else ""
        items = _home_cards(html)
        pages = _page_count(html, tid, pg)
        if not items:
            pages = pg                      # 空页刹车：不无限翻
        pages = max(pages, pg)
        return {"page": pg, "pagecount": pages, "limit": 72,
                "total": pages * 72, "list": items}

    # ---------- 搜索 ----------
    def searchContent(self, key, quick, pg="1"):
        s = self._s()
        pg = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        # 站点限制：搜索间隔 >= 3 秒
        gap = time.time() - getattr(s, "_last_search", 0)
        if gap < 4.0:
            time.sleep(4.0 - gap)
        s._last_search = time.time()
        if pg > 1:
            kw = "%s----------%d---" % (urllib.parse.quote(str(key or "")), pg)
        else:
            kw = urllib.parse.quote(str(key or "")) + "-------------"
        url = "%s/search/%s.html" % (s.host, kw)
        ck = "search:" + str(key) + ":" + str(pg)
        hit = s._cache.get(ck)
        if hit and time.time() - hit[0] < 90:
            return hit[1]
        st, body = s.get(url, referer=s.host + "/", tries=3)
        html = body.decode("utf-8", "replace") if body else ""
        items = _home_cards(html)
        if not items:                       # 网络抖动/限流 → 退避再试一轮
            time.sleep(4)
            st, body = s.get(url, referer=s.host + "/", tries=3)
            html = body.decode("utf-8", "replace") if body else ""
            items = _home_cards(html)
        if not items:
            pages = pg                      # 空页刹车
        else:
            m = re.search(r'title="尾页"', html)
            n2 = re.search(r'(\d+)---\.html"[^>]*title="尾页"', html)
            pages = int(n2.group(1)) if (m and n2) else pg + 1
        res = {"page": pg, "pagecount": pages, "limit": 72,
               "total": pages * 72, "list": items}
        if items:
            s._cache[ck] = (time.time(), res)
            if len(s._cache) > 40:
                s._cache.clear()
        return res

    # ---------- 详情 ----------
    def detailContent(self, ids):
        s = self._s()
        vid = str(ids[0]) if ids else ""
        vid = vid.split("@@")[-1].strip()
        if not vid.isdigit():
            return {"list": []}
        url = "%s/detail/%s.html" % (s.host, vid)
        st, body = s.get(url, referer=s.host + "/")
        html = body.decode("utf-8", "replace") if body else ""
        if not html or len(html) < 3000:
            return {"list": []}
        info = _detail_info(html)
        lines = _lines(html)
        eps = _episodes(html, vid)
        name = info.get("name") or ("第%s集" % vid)
        base = "%s/vplay/%s" % (s.host, vid)

        play_from, play_url = [], []
        if eps:
            for sid, items in eps:
                lname = lines[sid - 1] if 0 < sid <= len(lines) else ("线路%s" % sid)
                segs = []
                for n, nm in items:
                    label = "正片" if len(items) <= 1 else nm
                    segs.append("%s$%s-%d-%d.html" % (label, base, sid, n))
                play_from.append("歪比·%s" % lname)
                play_url.append("#".join(segs))
        if not play_url:
            play_from = ["歪比·主线"]
            play_url = ["在线观看$%s-1-1.html" % base]

        remark = " / ".join(info.get("tags", [])[:4])
        return {"list": [{
            "vod_id": vid,
            "vod_name": name,
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
        if not url.startswith("http"):
            if url.startswith("/"):
                url = s.host + url
            else:
                url = s.host + "/vplay/" + url
        flag = str(flag or "")

        # 1) 解析站解密（主线路）
        if "原页" not in flag and "嗅探" not in flag:
            try:
                m = re.search(r"/vplay/(\d+)-(\d+)-(\d+)\.html", url)
                if m:
                    vid, sid, nid = m.group(1), m.group(2), m.group(3)
                    ref = "%s/detail/%s.html" % (s.host, vid)
                    # 线路兜底：本线路 PID 失效（源站自己挂了）就换同集其它线路
                    cands = [sid] + [x for x in ("1", "2", "3") if x != sid]
                    for k, csid in enumerate(cands):
                        u = url if csid == sid else                             "%s/vplay/%s-%s-%s.html" % (s.host, vid, csid, nid)
                        st, body = s.get(u, referer=ref, tries=2)
                        html = body.decode("utf-8", "replace") if body else ""
                        cm = re.search(r'"url":"([A-Za-z0-9\-_]{40,})"', html)
                        if not cm:
                            continue
                        m3u8 = resolve_m3u8(s, cm.group(1))
                        if m3u8:
                            return {"parse": 0, "url": m3u8, "header": {
                                "User-Agent": UA, "Referer": PARSE_ORIGIN + "/"}}
                        if k < len(cands) - 1:
                            time.sleep(2.0)
            except Exception as e:
                s.last_err = "play:%s" % repr(e)[:60]

        # 2) 回落：交给 App 内建嗅探（原页）
        if "解析" in flag:
            return {"parse": 1, "url": url, "header": {}}
        return {"parse": 0, "url": url, "header": {
            "User-Agent": UA,
            "Referer": s.host + "/",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}}

    def localProxy(self, param=None):
        return [200, "application/json; charset=utf-8",
                json.dumps({"code": 1, "msg": "wbbb"}).encode("utf-8"), {}]
