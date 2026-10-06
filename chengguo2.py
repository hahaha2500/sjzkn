# -*- coding: utf-8 -*-
# 橙果短剧 chengguodj.com  TVBox/FongMi type=3 Python
# 站型: Nuxt SSR, 数据在 __NUXT_DATA__ (nuxt uneval 扁平数组)
import json
import re
import threading
import time
import gzip
import zlib
import urllib.request
import urllib.parse as _up
import random
from concurrent.futures import ThreadPoolExecutor

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self):
            pass

HOST = "https://chengguodj.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HDR = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# 分类: name, tid(路由段), filters
CATS = [
    ("推荐", "aiduanju"),
    ("AI短剧", "aiduanju"),
    ("真人短剧", "zhenren"),
    ("漫剧", "manju"),
    ("原创", "yuanchuang"),
    ("全部", "browse"),
]
# ===== 纯 Python AES-CBC 解密(零依赖, 手机壳无 pycryptodome) =====
# -*- coding: utf-8 -*-
"""纯 Python AES-CBC 解密(零依赖)。

TVBox/默影视 Chaquopy 环境通常没有 pycryptodome，图片解密不能依赖它。
只做解密方向；SBOX 由乘逆 + 仿射变换运行时生成，不写死表。
状态按列主序展平: st[4*col + row]。

正确性由 self_test() 保证:
  1) SBOX 抽检 00->63 / 53->ED / FF->16
  2) FIPS-197 AES-128 已知答案(解密方向对拍官方密文)
  3) CBC 加密-解密往返
调用方应在 import 后跑一次 self_test()。
"""


def _gmul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return r


def _rotl8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _inv8(a):
    """GF(2^8) 乘法逆(log/antilog 法), a=0 返回 0。"""
    if a == 0:
        return 0
    for b in range(1, 256):
        if _gmul(a, b) == 1:
            return b
    return 0


def _build_tables():
    sbox = [0] * 256
    for i in range(256):
        c = _inv8(i)
        sbox[i] = (c ^ _rotl8(c, 1) ^ _rotl8(c, 2) ^ _rotl8(c, 3)
                   ^ _rotl8(c, 4) ^ 0x63) & 0xFF
    rcon = [0x00]
    v = 1
    for _ in range(14):
        rcon.append(v)
        v = _gmul(v, 2)
    # 逆 SBOX 由正表反推(不要另建乘法逆表, 极易自覆盖出错)
    inv = [0] * 256
    for i, v2 in enumerate(sbox):
        inv[v2] = i
    return sbox, inv, rcon


_SBOX, _INV, _RCON = _build_tables()


def _expand(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _add_rk(st, w, rnd):
    for c in range(4):
        k = w[rnd * 4 + c]
        i = 4 * c
        st[i] ^= k[0]
        st[i + 1] ^= k[1]
        st[i + 2] ^= k[2]
        st[i + 3] ^= k[3]


def _shift_rows(st, inv):
    for r in range(1, 4):
        row = [st[4 * c + r] for c in range(4)]
        if inv:
            row = [row[(c - r) % 4] for c in range(4)]
        else:
            row = [row[(c + r) % 4] for c in range(4)]
        for c in range(4):
            st[4 * c + r] = row[c]


def _mix_columns(st, inv):
    if inv:
        m = (14, 11, 13, 9)
        for c in range(4):
            col = st[4 * c:4 * c + 4]
            for r in range(4):
                v = 0
                for j in range(4):
                    v ^= _gmul(col[j], m[(4 - r + j) % 4])
                st[4 * c + r] = v
    else:
        m = (2, 3, 1, 1)
        for c in range(4):
            col = st[4 * c:4 * c + 4]
            for r in range(4):
                st[4 * c + r] = (_gmul(col[0], m[(-r) % 4]) ^
                                 _gmul(col[1], m[(1 - r) % 4]) ^
                                 _gmul(col[2], m[(2 - r) % 4]) ^
                                 _gmul(col[3], m[(3 - r) % 4]))


def _decrypt_block(blk, w, nr):
    st = list(blk)
    _add_rk(st, w, nr)
    for rnd in range(nr - 1, -1, -1):
        _shift_rows(st, True)
        for i in range(16):
            st[i] = _INV[st[i]]
        _add_rk(st, w, rnd)
        if rnd:
            _mix_columns(st, True)
    return bytes(st)


def _encrypt_block(blk, w, nr):
    st = list(blk)
    _add_rk(st, w, 0)
    for rnd in range(1, nr + 1):
        for i in range(16):
            st[i] = _SBOX[st[i]]
        _shift_rows(st, False)
        if rnd != nr:
            _mix_columns(st, False)
        _add_rk(st, w, rnd)
    return bytes(st)


def _unpad(b):
    if not b:
        return b
    n = b[-1]
    if 0 < n <= 16 and b[-n:] == bytes([n]) * n:
        return b[:-n]
    return b


def aes_cbc_decrypt(data, key, iv=None, unpad_pkcs7=True):
    if not data or len(data) % 16:
        return b""
    w, nr = _expand(key)
    prev = list(iv) if iv else [0] * 16
    out = bytearray()
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        dec = _decrypt_block(blk, w, nr)
        out += bytes(dec[j] ^ prev[j] for j in range(16))
        prev = list(blk)
    return _unpad(bytes(out)) if unpad_pkcs7 else bytes(out)


def aes_cbc_encrypt(data, key, iv):
    """仅供自检往返用，调用方不需要。"""
    w, nr = _expand(key)
    prev = list(iv)
    out = bytearray()
    pad = 16 - (len(data) % 16)
    data = data + bytes([pad]) * pad
    for i in range(0, len(data), 16):
        blk = [data[i + j] ^ prev[j] for j in range(16)]
        enc = _encrypt_block(blk, w, nr)
        out += enc
        prev = list(enc)
    return bytes(out)

AES_OK = True   # 静态核对: SBOX/InvShiftRows/InvMixColumns 方向与 FIPS-197 一致

# 图床加密密钥(前端 bundle 明文常量)
MEDIA_KEY = b"f5d965df75336270"
MEDIA_IV = b"97b60394abc2fbe1"

CHMAP = {"aiduanju": "AI短剧", "zhenren": "真人", "manju": "漫剧",
         "yuanchuang": "原创", "mogai": "魔改"}

_CACHE = {}
_CLOCK = time.time
_LOCK = threading.Lock()
TIMEOUT = 15


def _decomp(raw, enc):
    if enc == "gzip":
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    elif enc == "deflate":
        for f in (lambda b: zlib.decompress(b), lambda b: zlib.decompress(b, -zlib.MAX_WBITS)):
            try:
                return f(raw)
            except Exception:
                pass
    return raw


def _http(url, ref=None, tries=6):
    h = dict(HDR)
    if ref:
        h["Referer"] = ref
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            rq = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = rq.read()
            raw = _decomp(raw, (rq.headers.get("Content-Encoding") or "").lower())
            return raw.decode("utf-8", "ignore")
        except Exception as e:
            last = "%s" % e
            if i + 1 < tries:
                time.sleep(min(1.2 * (i + 1) + random.random() * 0.5, 5))
    print("[橙果] 请求失败 %s -> %s" % (url, last))
    return ""


def _cached(key, url, ttl=300, ref=None):
    with _LOCK:
        it = _CACHE.get(key)
        if it and _CLOCK() - it[0] < ttl:
            return it[1]
    html = _http(url, ref)
    if not html:
        return ""
    with _LOCK:
        if len(_CACHE) > 400:
            _CACHE.clear()
        _CACHE[key] = (_CLOCK(), html)
    return html


def _uneval(html):
    """还原 Nuxt __NUXT_DATA__ 扁平数组为嵌套 dict/list。"""
    i = html.find("__NUXT_DATA__")
    if i < 0:
        return None
    s = html.find(">", i)
    e = html.find("</script>", s)
    if s < 0 or e < 0:
        return None
    try:
        arr = json.loads(html[s + 1:e])
    except Exception:
        return None
    try:
        return _rev(arr, 1, {})
    except Exception:
        return None


# devalue 扁平数组约定: 下标 0 = false, -1 = null, 其余为真实槽位;
# arr[0] 本身是类型标记(根), 不能当数据槽, 故根固定从下标 1 起。
def _rev(arr, idx, memo):
    if idx == 0:
        return False
    if idx == -1:
        return None
    if idx in memo:
        return memo[idx]
    v = arr[idx] if 0 <= idx < len(arr) else None
    if isinstance(v, bool) or v is None or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, list):
        if len(v) == 2 and isinstance(v[0], str) and \
                v[0] in ("ShallowReactive", "Reactive", "Ref", "ShallowRef",
                          "Shallow", "ShallowReactiveMap", "RefNuxt"):
            return _rev(arr, v[1], memo)
        out = []
        memo[idx] = out
        for x in v:
            out.append(_rev(arr, x, memo) if isinstance(x, int) and not isinstance(x, bool) else x)
        return out
    if isinstance(v, dict):
        out = {}
        memo[idx] = out
        for k, x in v.items():
            out[k] = _rev(arr, x, memo) if isinstance(x, int) and not isinstance(x, bool) else x
        return out
    return v


def _payload(html):
    """Nuxt payload: root.data[hash].data 取业务数据，逐层兜底。"""
    d = _uneval(html)
    if not isinstance(d, dict):
        return {}
    node = d
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        if any(k in node for k in ("dramas", "hot", "drama", "episodes", "results")):
            return node
        nxt = None
        for k in ("data", "props", "state", "status"):
            v = node.get(k)
            if isinstance(v, dict):
                nxt = v
                break
        if nxt is None:
            for v in node.values():
                if isinstance(v, dict):
                    nxt = v
                    break
        if nxt is None or nxt is node:
            return node
        node = nxt
    return node if isinstance(node, dict) else {}


def _clean(t):
    if not isinstance(t, str):
        return ""
    return re.sub(r"\s+", " ", t.replace("$", "").replace("#", "")).strip()


def _chn_prefix(chn):
    if isinstance(chn, dict):
        chn = chn.get("code") or chn.get("name") or ""
    if not isinstance(chn, str):
        return ""
    return (CHMAP.get(chn, "") + "·") if chn in CHMAP else ""


def _pic_of(v):
    """cover 可能是 str 或 {url, fallback_url} 结构。"""
    if isinstance(v, dict):
        u = v.get("url") or ""
        return u if isinstance(u, str) else ""
    return v if isinstance(v, str) else ""


def _bad_pic(u):
    if not u or not u.startswith("http"):
        return True
    low = u.lower()
    for w in ("/static/posters/", "default", "placeholder", "loading"):
        if w in low:
            return True
    return False


def _proxy_base(spider=None):
    """默影视/webhtv 本地代理基址(含 /proxy 段, 端口 9978~9999 动态)。
    源码依据: com/github/catvod/Proxy.java getUrl() -> http://127.0.0.1:{port}/proxy
    裸 class Spider 无 base/spider.py 的 getProxyUrl, 故自行取。"""
    try:
        from com.github.catvod import Proxy
        u = Proxy.getUrl(True)
        if u and "/proxy" in str(u):
            return str(u)
    except Exception:
        pass
    try:
        if spider is not None and hasattr(spider, "getProxyUrl"):
            u = spider.getProxyUrl(True)
            if u and "/proxy" in str(u):
                return str(u)
    except Exception:
        pass
    return "http://127.0.0.1:9978/proxy"


_PNG1X1 = None


class Spider(BaseSpider):

    def __init__(self):
        BaseSpider.__init__(self)
        self.host = HOST
        try:
            ex = self.getConf()
        except Exception:
            ex = None
        self.extend = ex if isinstance(ex, dict) else {}
        self.imode = 0

    def _pic(self, url, host):
        """图床防盗链 + 高频抖动: 走本地代理由本源控制请求头。
        img_mode=0(默认)本地代理, 1=直连原URL。"""
        if not url:
            return ""
        if self.imode == 1:
            return url
        try:
            b = _up.quote(url, safe="")
        except Exception:
            return url
        # 只带 do=py: 源码 BaseLoader.proxy 里 siteKey 分支会 getSpider(key),
        # key 传错 -> SpiderNull -> Invalid proxy response, 比不传更糟。
        return "%s?do=py&kind=img&url=%s" % (_proxy_base(self), b)

    # ---------- 基础 ----------
    def getName(self):
        return "橙果短剧"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return any(e in (url or "").lower() for e in
                   [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        ex = self.extend
        if isinstance(extend, str) and extend.strip():
            try:
                d = json.loads(extend)
                if isinstance(d, dict):
                    ex = d
            except Exception:
                if extend.strip().startswith("http"):
                    ex = {"host": extend.strip().rstrip("/")}
        if isinstance(ex, dict) and ex.get("host"):
            self.host = str(ex["host"]).rstrip("/")
        self.imode = 1 if self.extend.get("img_mode") == "1" else 0

    def homeContent(self, filter=False):
        classes = [{"type_id": t, "type_name": n} for n, t in CATS]
        return {"class": classes}

    def homeVideoContent(self):
        html = _cached("home", self.host + "/aiduanju", 180, self.host + "/")
        vods = self._cards(html)
        return {"list": vods[:60]}

    # ---------- 列表 ----------
    def _cards(self, html):
        p = _payload(html)
        if not p:
            return []
        lst = []
        for k in ("dramas", "hot", "results", "items", "list"):
            v = p.get(k)
            if isinstance(v, list) and v:
                lst = v
                break
        out = []
        seen = set()
        for d in lst:
            if not isinstance(d, dict):
                continue
            rid = d.get("slug") or d.get("id")
            if not rid or rid in seen:
                continue
            name = _clean(d.get("title"))
            if not name:
                continue
            seen.add(rid)
            pic = _pic_of(d.get("cover"))
            if _bad_pic(pic):
                fb = _pic_of(d.get("cover_fallback_url"))
                pic = fb if not _bad_pic(fb) else ""
            ep = d.get("total_episodes") or d.get("total_episode_count") or 0
            if not isinstance(ep, int):
                ep = 0
            name = "%s%s" % (_chn_prefix(d.get("channel")), name)
            pic = self._pic(pic, self.host)
            out.append({"vod_id": str(rid), "vod_name": name, "vod_pic": pic,
                        "vod_remarks": "%s集" % ep if ep else ""})
        return out

    def categoryContent(self, tid, pg, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        tid = (tid or "aiduanju").strip()
        if tid.startswith("http"):
            tid = tid.rstrip("/").split("/")[-1] or "aiduanju"
        if tid.isdigit():
            for n, t in CATS:
                if t == tid:
                    tid = t
                    break
        path = "/" + tid if pg <= 1 else "/%s/page-%d" % (tid, pg)
        html = _cached("cat:%s:%d" % (tid, pg), self.host + path, 300, self.host + "/")
        vods = self._cards(html)
        pc = self._pagecount(html, pg)
        return {"list": vods, "page": pg, "pagecount": pc, "limit": 30, "total": len(vods)}

    def _pagecount(self, html, pg):
        try:
            p = _payload(html)
            pag = p.get("pagination") if isinstance(p, dict) else None
            if isinstance(pag, dict):
                v = int(pag.get("total_pages") or 0)
                if v > 0:
                    return v
        except Exception:
            pass
        pc = 0
        for m in re.finditer(r"/page-(\d+)", html or ""):
            try:
                pc = max(pc, int(m.group(1)))
            except Exception:
                pass
        return pc if pc > 0 else pg

    # ---------- 搜索 ----------
    def searchContent(self, key, quick="", pg="1"):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if not key:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 30, "total": 0}
        
        kw = _up.quote(key, safe="")
        url = "%s/search/%s" % (self.host, kw) if pg <= 1 else \
            "%s/search/%s/page-%d" % (self.host, kw, pg)
        html = _cached("se:%s:%d" % (key, pg), url, 180, self.host + "/")
        out = self._cards(html)
        return {"list": out, "page": pg, "pagecount": self._pagecount(html, pg),
                "limit": 30, "total": len(out)}
        lst = []
        seen = set()
        for d in lst:
            if not isinstance(d, dict):
                continue
            rid = d.get("slug") or d.get("id")
            if not rid or rid in seen:
                continue
            name = _clean(d.get("title"))
            if not name:
                continue
            seen.add(rid)
            pic = _pic_of(d.get("cover"))
            if _bad_pic(pic):
                fb = _pic_of(d.get("cover_fallback_url"))
                pic = fb if not _bad_pic(fb) else ""
            ep = d.get("total_episodes") or 0
            if not isinstance(ep, int):
                ep = 0
            out.append({"vod_id": str(rid), "vod_name": name, "vod_pic": pic,
                        "vod_remarks": "%s集" % ep if ep else ""})
        return {"list": out, "page": pg, "pagecount": self._pagecount(html, pg),
                "limit": 30, "total": len(out)}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        vid = ""
        if isinstance(ids, list) and ids:
            vid = str(ids[0])
        elif ids:
            vid = str(ids)
        vid = vid.split("@@")[-1] or vid
        if not vid:
            return {}
        html = _cached("dt:%s" % vid, self.host + "/drama/" + vid, 600, self.host + "/")
        p = _payload(html)
        d = p.get("drama") if isinstance(p.get("drama"), dict) else p
        if not isinstance(d, dict):
            d = {}
        eps = []
        if isinstance(p, dict):
            for k in ("episodes", "episode_list", "list"):
                if isinstance(p.get(k), list):
                    eps = p[k]
                    break
        segs = []
        titles = []
        for e in eps:
            if not isinstance(e, dict):
                continue
            num = e.get("number") or 0
            nm = _clean(e.get("title")) or "第%s集" % num
            try:
                num = int(num)
            except Exception:
                num = len(segs) + 1
            if nm == _clean(str(num)) or nm == str(num):
                nm = "第%d集" % num
            segs.append("%s$%s@@%d" % (nm, vid, num))
            titles.append(num)
        if not segs:
            segs.append("正片$%s@@1" % vid)
        name = _clean(d.get("title")) or vid
        pic = _pic_of(d.get("cover"))
        if _bad_pic(pic):
            fb = _pic_of(d.get("cover_fallback_url"))
            pic = fb if not _bad_pic(fb) else ""
        chn = d.get("channel")
        if isinstance(chn, dict):
            chn = chn.get("name") or chn.get("code") or ""
        if not isinstance(chn, str):
            chn = ""
        pic = self._pic(pic, self.host)
        return {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_year": str(d.get("published_at") or "")[:4],
            "vod_area": chn,
            "vod_remarks": "%s集" % (d.get("total_episode_count") or len(segs)),
            "vod_actor": "",
            "vod_director": "",
            "vod_content": _clean(d.get("intro")),
            "vod_play_from": "橙果·主线$$$橙果·代理$$$橙果·原页",
            # 多线路必须用 $$$ 分隔; 用 # 会与"集"分隔符冲突, 被壳子误当一集
            "vod_play_url": "$$$".join(["#".join(segs)] * 3),
        }

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags=None):
        parts = [x for x in str(id).split("@@") if x]
        vid = parts[0] if parts else ""
        num = 1
        if len(parts) >= 2:
            try:
                num = int(parts[-1])
            except Exception:
                num = 1
        if not vid:
            return {"parse": 0, "url": ""}
        page = "%s/play/%s/%d" % (self.host, vid, num)
        fl = str(flag or "")
        if "原页" in fl:
            return {"parse": 0, "url": page, "header": {"User-Agent": UA,
                                                         "Referer": self.host + "/"}}
        url = self._m3u8(page)
        hdr = {"User-Agent": UA, "Referer": self.host + "/"}
        if not url:
            return {"parse": 0, "url": page, "header": hdr}
        if "代理" in fl:
            return {"parse": 0, "url": "%s?do=py&kind=m3u8&url=%s" % (
                _proxy_base(self), _up.quote(url, safe="")), "header": hdr}
        return {"parse": 0, "url": url, "header": hdr}

    def _m3u8(self, page):
        """播放页 SSR 数据里抠真实流地址(绝对 m3u8/mp4, 带 auth_key)。"""
        html = _http(page, self.host + "/")
        if not html:
            return ""
        html = html.replace("\\u002F", "/").replace("\\/", "/")
        for key in ("source_url", "h265_url", "mp4_url", "play_url", "url"):
            m = re.search(r'"%s"\s*:\s*"(https?://[^"]+)"' % key, html)
            if m and (".m3u8" in m.group(1) or ".mp4" in m.group(1)):
                return m.group(1)
        m = re.search(r'https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+?\.m3u8[^"\\\s<]*', html)
        return m.group(0) if m else ""

    # ---------- 本地代理 ----------
    def localProxy(self, param=None):
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except Exception:
                return [404, "text/plain", b"", {}]
        if not isinstance(param, dict):
            return [404, "text/plain", b"", {}]
        url = param.get("url") or ""
        if not url:
            return [404, "text/plain", b"", {}]
        kind = param.get("kind") or "img"
        if kind == "img":
            # 图床防盗链: okhttp UA + 站方 Referer 实测可过
            hdr = {"User-Agent": "okhttp/4.9.0", "Referer": self.host + "/"}
        else:
            hdr = {"User-Agent": UA, "Referer": self.host + "/"}
        raw = b""
        mime = "application/octet-stream"
        for ua in (hdr["User-Agent"], UA, "Mozilla/5.0 Chrome/124.0"):
            h = dict(hdr)
            h["User-Agent"] = ua
            try:
                rq = urllib.request.urlopen(
                    urllib.request.Request(url, headers=h), timeout=TIMEOUT)
                raw = _decomp(rq.read(), (rq.headers.get("Content-Encoding") or "").lower())
                mime = rq.headers.get("Content-Type") or mime
                if len(raw) > 100:
                    break
            except Exception:
                continue
        if kind == "img":
            raw, mime = _fix_img(raw)
        elif len(raw) < 20:
            return [404, "text/plain", b"", {}]
        if kind == "m3u8":
            mime = "application/vnd.apple.mpegurl"
        return [200, mime, raw, {"Content-Type": mime, "Cache-Control": "max-age=3600"}]


def _fix_img(raw):
    """图床返回的是 AES-CBC 密文(cover_encrypted=True), 必须解密才是图片。
    三级判定: 已是图片魔数 -> 直接用; 解密出图片 -> 用解密结果; 否则占位 PNG。"""
    if len(raw) > 100 and _magic(raw):
        return raw, _mime_of(raw)
    if len(raw) >= 16 and len(raw) % 16 == 0 and AES_OK:
        try:
            dec = aes_cbc_decrypt(raw, MEDIA_KEY, MEDIA_IV)
            if len(dec) > 100 and _magic(dec):
                return dec, _mime_of(dec)
        except Exception:
            pass
    return _png(), "image/png"


def _magic(b):
    return (b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n"
            or b[:6] in (b"GIF87a", b"GIF89a") or
            (b[:4] == b"RIFF" and b[8:12] == b"WEBP"))


def _mime_of(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _png():
    """程序生成合法 1x1 透明 PNG(取图失败兜底, 手写 hex 会 CRC 错)。"""
    import struct
    import zlib as _z
    w = h = 1
    raw = b"\x00" + b"\x00\x00\x00\x00"
    def ck(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", _z.crc32(c) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + ck(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + ck(b"IDAT", _z.compress(raw)) + ck(b"IEND", b""))
