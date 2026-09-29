# -*- coding: utf-8 -*-
"""
dida.py — 嘀嗒影视（didahd.xyz）TVBox / FongMi Chaquopy Python type=3 源

═══════════════ 站点说明书（逆向要点，换站/修站前先读这段）═══════════════
【站型】苹果CMS v10 + myui 模板（自定义 URL 层），**无采集接口**
        /api.php/provide/vod/ 实测 404/空 → 纯 HTML 直抓（不要走采集 API）。

【URL 规律（全部实测）】
  分类首页   /type/{tid}.html                1电影 2电视剧 3纪录片 4动漫 5综艺
  列表/筛选  /show/{12段}.html                ← 固定 12 段位
             段位: 1=tid 2=地区area 3=排序by 4=剧情class 5=语言lang 6=字母letter
                   9=页码page 12=年份year（7/8/10/11 保留恒为空）
             第N页 = /show/{tid}--------{N}---.html   （实测首页/上一页/尾页都是这个款）
  详情       /detail/{id}.html
  播放       /play/{vid}-{sid}-{nid}.html     sid=线路号(1夸克 2百度 3UC 4~6视频线...)
  搜索       /search/{quote(kw)}----------{pg}---.html   ← 14 段位, 段1=关键词, 段11=页码
             搜索页内 a[演员]=段2、a[导演]=段6（同模板不同段位, 本源只用关键词段）
  筛选区     /type/{tid}.html 里 <ul class="myui-screen__list"> 每组第一条无 href 是组名

【★播放链（本源核心逆向, 4 步, 纯 Python 已 100% 复现）】
  ① 播放页 HTML 里 `var player_aaaa={...}` 平衡括号抠出 JSON：
     { encrypt:3, url:"<hex密文>", from:"BBA", sid, nid, link, vod_data }
  ② 拼 iframe 真实播放器页  /static/player/artplayer/?url={hex}
     页面顶部明文常量:  const playPageUrl="<b64 vkey>"  const timestamp="<秒>"
                        const secretKeySeed="<code>"  const videoHash=... const vidHash=...
  ③ POST https://hd.ticktockwow.com/smartplay-cache/api/webvideo_ty.php
     body  = {"vkey":playPageUrl, "code":secretKeySeed, "t":当前秒, "signature":MD5(str(t))}
     ★必带 Header:  Content-Type: application/json   +   Origin: https://www.didahd.xyz
       （实测缺 Origin → 403 {"error":""}；只带 Referer 不行）
     ★t 有约 60 秒时效：过期 → 403 {"error":"Invalid or expired timestamp"}
     ★signature 就是 MD5(t)（两组样本实测命中, 无盐）
     回包 {code:200, url:"<b64密文>", subtitle, cache}
  ④ AES-128-CBC 解密 url：
     md5hex = MD5( ②的 timestamp + "RY7e48naFXPsLJC" )   ← 32 位 hex（盐是固定常量）
     iv  = md5hex[0:16]   key = md5hex[16:32]            ← 注意: iv 在前 key 在后！
     两组样本校验:
       ts=1790652960 → md5=96757d910b5abdeb8c11c08bc2adef19 → key=8c11c08bc2adef19 iv=96757d910b5abdeb ✅
       ts=1790654015 → md5=38fe7440f24a48955d1632203e15d8d5 → key=5d1632203e15d8d5 iv=38fe7440f24a4895 ✅
     明文 = 真实 mp4/m3u8 直链（实测 bytetos mp4 无防盗链, 裸请求 206 可播）

【线路结构（同一详情页实测）】
  视频线 from ∈ {BBA, rrmj, NBY, 4kvm, ...} → 它们的 /static/player/{from}.js 全部是
        同一句: iframe /static/player/artplayer/?url=... → 走上面 4 步解密链 ✅
  网盘线 from ∈ {ucpan, quark, baidu} → iframe /static/player/pan.html（二维码/跳转页, 无直链）
        真链接在详情页「视频下载」区明文给出: drive.uc.cn / pan.quark.cn / pan.baidu.com
  → 本源: 视频线=解密出直链 parse:0（解密失败回落 parse:1 交宿主解析）
          另附「网页嗅探」兜底线 + 网盘分享线（需壳子支持网盘解析）

【图片】img12.360buyimg.com / img.meituan.net / gimg0.baidu.com 直链
        懒加载 data-original（首页热门区有直接 <img src>），跳过 load.png 占位

【本文件技术约定（默影视加载层铁律 + 交付契约）】
  · getDependence() 必须返回 []（返回非空会让壳子装依赖失败 → 整源白屏）
  · 显式 __init__ 且先 super/BaseSpider.__init__，继承别名 BaseSpider（T4 双模式 try import）
  · localProxy(param=None) 带默认参数，返回四元组 [status, mime, bytes, header]
  · header 一律 dict（绝不能 json.dumps 字符串）
  · requests 可选（文件头 try import 自兜底，getDependence 仍返回 []）
  · 分隔符铁律 $=名称/地址  #=多集  $$$=多线路，线路名与地址段数必须相等
  · 返回契约 list/page/pagecount/limit/total，空页也回 pagecount（空页刹车防无限翻）
"""

import re
import json
import time
import base64
import hashlib
import gzip
import io

from urllib.parse import quote, unquote, urljoin

try:                                    # requests 可选：有就用（连接复用, API 那步 TLS 很慢）
    import requests                     # 没有也不报错, 自动回落 urllib
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False

try:                                    # pycryptodome 可选：有就用
    from Crypto.Cipher import AES as _PAES
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False

try:
    from base.spider import Spider as BaseSpider       # T4 / FongMi 宿主
except Exception:
    class BaseSpider(object):                          # 裸壳 / 本地自测
        def __init__(self):
            pass


# ═══════════════════ 站点常量 ═══════════════════
NAME = "嘀嗒影视"
DOMAINS = ["https://www.didahd.xyz", "https://didahd.xyz"]
API_URL = "https://hd.ticktockwow.com/smartplay-cache/api/webvideo_ty.php"
SALT = "RY7e48naFXPsLJC"               # ★AES 盐, 逆向所得, 固定值
ORIGIN = "https://www.didahd.xyz"      # ★API 必带 Origin（白名单按站点主域校验）
UA = ("Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

FALLBACK_CLASS = [                     # 首页抓不到分类时的兜底
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "电视剧"},
    {"type_id": "3", "type_name": "纪录片"},
    {"type_id": "4", "type_name": "动漫"},
    {"type_id": "5", "type_name": "综艺"},
]

# 12 段位 → filters 键名（段位下标 0-based）
SEG_KEY = {0: "sub", 1: "area", 2: "by", 3: "class", 4: "lang", 5: "letter", 11: "year"}
KEY_SEG = dict((v, k) for k, v in SEG_KEY.items())


# ═══════════════════ 纯 Python AES-128-CBC（无 pycryptodome 依赖）═══════════════════
_SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i


def _gmul(x, y):
    """GF(2^8) 乘法（AES 用）"""
    r = 0
    while y:
        if y & 1:
            r ^= x
        x <<= 1
        if x & 0x100:
            x ^= 0x11b
        y >>= 1
    return r & 0xff


def _expand_key_128(key16):
    """AES-128 密钥扩展 → 176 字节（11 个轮密钥）"""
    w = list(key16)
    rcon = 1
    for i in range(4, 44):
        t = w[(i - 1) * 4:i * 4]
        if i % 4 == 0:
            t = t[1:] + t[:1]                       # RotWord
            t = [_SBOX[b] for b in t]               # SubWord
            t[0] ^= rcon
            rcon = _gmul(rcon, 2)
        for j in range(4):
            w.append(w[(i - 4) * 4 + j] ^ t[j])
    return w


def _inv_mix_col(a0, a1, a2, a3):
    return (
        _gmul(a0, 0x0e) ^ _gmul(a1, 0x0b) ^ _gmul(a2, 0x0d) ^ _gmul(a3, 0x09),
        _gmul(a0, 0x09) ^ _gmul(a1, 0x0e) ^ _gmul(a2, 0x0b) ^ _gmul(a3, 0x0d),
        _gmul(a0, 0x0d) ^ _gmul(a1, 0x09) ^ _gmul(a2, 0x0e) ^ _gmul(a3, 0x0b),
        _gmul(a0, 0x0b) ^ _gmul(a1, 0x0d) ^ _gmul(a2, 0x09) ^ _gmul(a3, 0x0e),
    )


def _aes128_decrypt_block(block16, rk):
    """单块 AES-128 解密（列主序 state[r + 4c]）"""
    s = list(block16)
    for i in range(16):
        s[i] ^= rk[160 + i]                         # AddRoundKey(10)
    for rnd in range(9, 0, -1):
        # InvShiftRows: 行 r 右移 r 列
        t = s[:]
        for r in range(1, 4):
            for c in range(4):
                s[r + 4 * c] = t[r + 4 * ((c - r) % 4)]
        # InvSubBytes
        for i in range(16):
            s[i] = _INV_SBOX[s[i]]
        # AddRoundKey(rnd)
        for i in range(16):
            s[i] ^= rk[rnd * 16 + i]
        # InvMixColumns
        for c in range(4):
            b0, b1, b2, b3 = _inv_mix_col(s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3])
            s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3] = b0, b1, b2, b3
    t = s[:]
    for r in range(1, 4):
        for c in range(4):
            s[r + 4 * c] = t[r + 4 * ((c - r) % 4)]
    for i in range(16):
        s[i] = _INV_SBOX[s[i]]
    for i in range(16):
        s[i] ^= rk[i]                               # AddRoundKey(0)
    return bytes(s)


def _pkcs7_unpad(data):
    if not data:
        raise ValueError("empty")
    n = data[-1]
    if not isinstance(n, int):
        n = ord(n)
    if n < 1 or n > 16 or data[-n:] != bytes([n]) * n:
        raise ValueError("bad pkcs7")
    return data[:-n]


def aes_cbc_decrypt(raw, key, iv):
    """AES-128-CBC 解密 + PKCS7 去填充。优先用 pycryptodome, 没有就用纯 Python 实现。"""
    if _HAS_CRYPTO:
        pt = _PAES.new(key, _PAES.MODE_CBC, iv).decrypt(raw)
    else:
        rk = _expand_key_128(key)
        out = bytearray()
        prev = iv                                # ★CBC 链: 明文 = 解密块 XOR 前一密文块
        for i in range(0, len(raw), 16):
            blk = raw[i:i + 16]
            d = _aes128_decrypt_block(blk, rk)
            out += bytes(d[j] ^ prev[j] for j in range(16))
            prev = blk
        pt = bytes(out)
    return _pkcs7_unpad(pt)


# ═══════════════ 模块级共享状态 ═══════════════
# ★★ 关键: TVBox/FongMi 某些壳每次调用接口都会 new 一个 Spider 实例,
#     若 session/缓存挂在实例上 → 每次都重新 TLS 握手(该 API 冷连接实测 25~38 秒)且缓存全丢。
#     所以连接池、缓存、预热状态一律挂模块级(同进程内跨实例共享)。
_CACHE = {}                                   # {key: (expire_ts, value)}
_SESSION = None
_WARM = {"ts": 0.0, "running": False}
API_ROOT = "https://hd.ticktockwow.com/"      # 预热用（与取链 API 同域同端口, 连接可复用）


def _get_session():
    """全进程共用一个 requests.Session → 连接池只握手一次。"""
    global _SESSION
    if _SESSION is None and _HAS_REQ:
        try:
            sess = requests.Session()
            sess.headers.update({
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
            })
            try:
                from urllib3.util.retry import Retry
                from requests.adapters import HTTPAdapter
                r = Retry(total=2, backoff_factor=0.4,
                           status_forcelist=[429, 500, 502, 503, 504],
                           allowed_methods=["GET", "POST"])
                ad = HTTPAdapter(pool_connections=6, pool_maxsize=10, max_retries=r)
                sess.mount("http://", ad)
                sess.mount("https://", ad)
            except Exception:
                pass
            _SESSION = sess
        except Exception:
            _SESSION = None
    return _SESSION


# ═══════════════════ Spider ═══════════════════
class Spider(BaseSpider):

    def __init__(self):
        BaseSpider.__init__(self)
        self._host = ""
        self._ua = UA
        self._cache = _CACHE                    # ★指向模块级缓存(跨实例共享)
        self._sess = _get_session()             # ★指向模块级连接池(跨实例共享)

    # ───────────── 基础契约 ─────────────
    def getDependence(self):
        return []                                   # ★绝不能返回 ["requests"], 否则整源白屏

    def getName(self):
        return NAME

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower().split("?")[0]
        return any(u.endswith(ext) for ext in
                   (".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg", ".wmv", ".webm"))

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        host = ""
        if isinstance(extend, dict):
            host = str(extend.get("host") or "")
        elif isinstance(extend, str) and extend.strip():
            s = extend.strip()
            if s.startswith("{"):
                try:
                    host = str(json.loads(s).get("host") or "")
                except Exception:
                    host = ""
            elif "http" in s:
                host = s
        if host:
            host = host.rstrip("/")
            if not host.startswith("http"):
                host = "https://" + host
            self._host = host
        else:
            self._host = DOMAINS[0]
        self._warm_api()                         # 一进源就开始预热 API 连接（不阻塞）
        return self._host

    def _get_host(self):
        if not self._host:
            self._host = DOMAINS[0]
        return self._host

    # ───────────── 网络单入口 ─────────────
    def _http(self, url, referer=None, timeout=20, tries=1):
        """统一 GET → 文本。域名池容灾（主域失败自动切镜像, 池内每域试一次）。"""
        hosts = [self._get_host()] + [d for d in DOMAINS if d != self._get_host()]
        last = ""
        for h in hosts:
            real = url.replace(self._get_host(), h) if self._get_host() in url else url
            for k in range(tries):
                try:
                    txt = self._fetch_one(real, referer, h, timeout)
                    if txt:
                        return txt
                    last = "empty"
                except Exception as e:
                    last = "%s: %s" % (type(e).__name__, e)
                    time.sleep(0.3)
            # 该域失败 → 换下一个镜像
        raise RuntimeError(last or "fetch failed")

    def _fetch_one(self, url, referer, host, timeout):
        headers = {"User-Agent": self._ua}
        headers["Accept-Encoding"] = "gzip"        # 站点支持 gzip(首页 81KB→13KB, 省 84% 流量)
        if referer:
            headers["Referer"] = referer
        if self._sess is not None:
            # ★超时一律用标量: 本机 requests/urllib3 对 tuple 语义不可靠(会被当成 read=connect)
            r = self._sess.get(url, headers=headers, timeout=timeout, verify=False)
            if r.status_code >= 400:
                raise RuntimeError("http %s" % r.status_code)
            r.encoding = "utf-8"
            return self._unwrap(r.text)
        import urllib.request
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
        try:
            return self._unwrap(raw.decode("utf-8"))
        except Exception:
            return self._unwrap(raw.decode("gbk", "ignore"))

    @staticmethod
    def _unwrap(txt):
        """解包 JSON 字符串响应。
        实测: 分类页 tid=2（电视剧）等会返回 `"<!DOCTYPE html>\\n...<\\/a>\\u7c7b..."` 这种
        被 json.dumps 过的整页 HTML（服务端变体, curl 裸抓也一样）→ 必须先解包再解析。"""
        if not txt:
            return txt
        t = txt.lstrip()
        if not t or t[0] not in '"{':
            return txt
        try:
            obj = json.loads(t)
        except Exception:
            return txt
        if isinstance(obj, str) and ("<" in obj or "&" in obj):
            return obj
        if isinstance(obj, dict):
            for k in ("html", "content", "data", "body", "page", "result"):
                v = obj.get(k)
                if isinstance(v, str) and "<" in v:
                    return v
                if isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, str) and "<" in vv and len(vv) > 500:
                            return vv
        return txt

    def _post_json(self, url, obj, referer=None, origin=None, timeout=30):
        """POST JSON。obj 可以是 dict, 也可以是「每次调用重新生成 payload 的 callable」。
        ★关键: 该 API 的 t 只有约 60 秒时效, 慢响应/重试都会让 t 变陈旧 → 必须每次尝试现算。"""
        headers = {
            "User-Agent": self._ua,
            "Content-Type": "application/json",
        }
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer
        last = ""
        for attempt in range(2):                   # ★服务端 13~28 秒才回: 长超时 + 少量重试(控总时长)
            payload = obj() if callable(obj) else obj
            data = json.dumps(payload).encode("utf-8")
            try:
                if self._sess is not None:
                    r = self._sess.post(url, headers=headers, data=data,
                                        timeout=timeout, verify=False)
                    body = r.text
                    if r.status_code < 500 and not self._is_stale(body):
                        return r.status_code, body
                    last = "http %s %s" % (r.status_code, body[:80])
                    continue
                import urllib.request
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                        body = resp.read().decode("utf-8", "ignore")
                        if not self._is_stale(body):
                            return resp.status, body
                        last = "stale %s" % body[:80]
                        continue
                except Exception as e:
                    code = getattr(e, "code", 0)
                    body = ""
                    try:
                        body = e.read().decode("utf-8", "ignore")
                    except Exception:
                        pass
                    if code and code < 500 and not self._is_stale(body):
                        return code, body
                    last = str(e)
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(last or "post failed")

    @staticmethod
    def _is_stale(body):
        """命中 t 过期错误 → 视为可重试"""
        return bool(body) and ("timestamp" in body or "expired" in body)

    def _warm_api(self):
        """★后台预热取链 API 的 TLS 连接。
        实测该主机冷连接要 25~38 秒(DNS 5.6 + TLS 13~19), 连接复用后只要 0.8 秒。
        在 init/首页/分类/搜索/详情 各触发一次(60 秒内不重复), 全程不阻塞界面,
        这样用户真正点播时连接已就绪 → 取链从 ~25 秒降到 ~1 秒。"""
        now = time.time()
        if _WARM.get("running") or (now - _WARM.get("ts", 0) < 60):
            return
        _WARM["running"] = True
        _WARM["start"] = time.time()

        def _work():
            try:
                if self._sess is not None:
                    self._sess.get(API_ROOT, timeout=30, verify=False)
                else:
                    import urllib.request
                    import ssl as _ssl
                    ctx = _ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = _ssl.CERT_NONE
                    req = urllib.request.Request(API_ROOT, headers={"User-Agent": self._ua})
                    urllib.request.urlopen(req, timeout=30, context=ctx).read(64)
            except Exception:
                pass
            finally:
                _WARM["ts"] = time.time()          # 失败也记时间, 60 秒后才重试(防止刷请求)
                _WARM["running"] = False

        try:
            import threading
            th = threading.Thread(target=_work, name="dida-warm")
            th.daemon = True
            th.start()
        except Exception:
            _WARM["running"] = False

    def _wait_warm(self, max_wait=12.0, min_age=4.0):
        """预热快好时稍等一下, 直接复用它建好的连接。
        背景: 该 API 主机冷连接要 15~25 秒, 复用只要 0.8 秒; 若预热和取链各开一条冷连接就白费一倍时间。
        只在预热已跑 ≥4 秒(大概率 TLS 已建立、马上就好)时才等, 最多等 12 秒, 超时就自己发。"""
        start = _WARM.get("start", 0) or 0
        if not _WARM.get("running") or start <= 0:
            return
        if time.time() - start < min_age:
            return
        deadline = time.time() + max_wait
        while _WARM.get("running") and time.time() < deadline:
            time.sleep(0.2)

    # ───────────── 缓存 ─────────────
    def _cget(self, key):
        v = self._cache.get(key)
        if v and v[0] > time.time():
            return v[1]
        return None

    def _cset(self, key, val, ttl=300):
        if len(self._cache) > 400:
            now = time.time()
            self._cache = dict((k, v) for k, v in self._cache.items() if v[0] > now)
        self._cache[key] = (time.time() + ttl, val)
        return val

    # ───────────── 通用解析工具 ─────────────
    @staticmethod
    def _clean(html):
        """去 HTML 注释（模板里有 <!--...pic-text...--> 假节点, 会污染正则）"""
        return re.sub(r"<!--.*?-->", "", html or "", flags=re.S)

    @staticmethod
    def _strip_tags(s):
        s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
        s = s.replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def _norm_name(s):
        """集名/片名清洗: 不能含 $ # (TVBox 分隔符)"""
        if not s:
            return ""
        s = str(s).replace("$", "").replace("#", "").replace("\n", " ").strip()
        return re.sub(r"\s+", " ", s)

    @staticmethod
    def _balanced(text, start):
        """从 start(指向第一个 { )做括号平衡扫描, 禁贪婪正则吞整页"""
        depth = 0
        for j in range(start, len(text)):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:j + 1]
        return None

    def _cards(self, html):
        """列表卡解析（首页/分类/搜索通用）。返回 vod dict 列表。"""
        html = self._clean(html)
        out, seen = [], set()
        # ① 网格卡 myui-vodlist__box（首页、分类页）
        chunks = html.split('<div class="myui-vodlist__box"')
        # ② 搜索结果 media 列表
        if len(chunks) < 2 and 'id="searchList"' in html:
            seg = html.split('id="searchList"', 1)[1]
            seg = seg.split("</ul>", 1)[0]
            chunks = ["<li" + c for c in seg.split("<li")]
        for ch in chunks[1:]:
            v = self._card(ch)
            if v and v.get("vod_id") not in seen:
                seen.add(v["vod_id"])
                out.append(v)
        return out

    def _card(self, ch):
        try:
            # 截断到卡结束, 避免吃到下一个卡
            m = re.search(r'href="(?:https?://[^"]*)?/detail/(\d+)\.html"', ch)
            if not m:
                return None
            vid = m.group(1)
            head = ch[:3000]
            # 封面: 多字段兜底, 跳过占位图
            pic = ""
            am = re.search(r'<a[^>]+href="[^"]*/detail/' + vid + r'\.html"[^>]*>', head)
            if am:
                tag = am.group(0)
                for attr in ("data-original", "data-src", "data-lazy-src", "data-poster", "src"):
                    pm = re.search(attr + r'="([^"]+)"', tag)
                    if pm:
                        cand = pm.group(1).strip()
                        if cand and "load.png" not in cand and "loading" not in cand:
                            pic = cand
                            break
            if not pic:
                im = re.search(r'<img[^>]+>', head)
                if im:
                    for attr in ("data-original", "data-src", "data-lazy-src", "src"):
                        pm = re.search(attr + r'="([^"]+)"', im.group(0))
                        if pm and "load.png" not in pm.group(1):
                            pic = pm.group(1)
                            break
            if pic:
                pic = pic.replace("&amp;", "&")
                if pic.startswith("//"):
                    pic = "https:" + pic
                elif pic.startswith("/"):
                    pic = urljoin(self._get_host() + "/", pic)
            # 片名: 卡 a[title] → h4>a 文本
            name = ""
            if am:
                nm = re.search(r'\btitle="([^"]+)"', am.group(0))
                if nm:
                    name = nm.group(1)
            if not name:
                nm = re.search(r'<h4[^>]*>\s*<a[^>]*>([^<]+)</a>', head)
                if nm:
                    name = self._strip_tags(nm.group(1))
            if not name:
                return None
            # 备注: pic-text
            rm = re.search(r'pic-text[^>]*>([^<]*)<', head)
            remarks = self._strip_tags(rm.group(1)) if rm else ""
            # 评分
            sm = re.search(r'pic-tag[^>]*>([^<]*)<', head)
            score = self._strip_tags(sm.group(1)) if sm else ""
            # 副标题（别名行 / 搜索页又名·导演·主演）
            sub = ""
            pm = re.search(r'<p class="text text-overflow[^"]*">([\s\S]*?)</p>', head)
            if pm:
                sub = self._strip_tags(pm.group(1))
            else:
                parts = []
                for k in ("又名", "导演", "主演", "类型"):
                    km = re.search(r'>' + k + r'：</span>([\s\S]*?)(?:</p>|$)', head)
                    if km:
                        parts.append(k + " " + self._strip_tags(km.group(1))[:60])
                sub = " / ".join(parts)
            if not remarks and score:
                remarks = score
            return {
                "vod_id": vid,
                "vod_name": self._norm_name(name),
                "vod_pic": pic,
                "vod_remarks": self._norm_name(remarks),
                "vod_score": score.replace("分", ""),
                "vod_en": self._norm_name(sub),
            }
        except Exception:
            return None

    def _pagecount(self, html, kind="show"):
        """从『尾页』链接抠总页数。kind=show(12段) / search(14段)"""
        html = self._clean(html)
        if kind == "search":
            pat = r"href=\"[^\"]*/search/[^\"']*?-{2,}(\d+)-{2,}\.html\"[^>]*>\s*尾页"
            m = re.search(pat, html)
            if not m:
                m = re.search(r"href=\"[^\"]*/search/[^\"]*?(\d+)-{3,}\.html\"[^>]*>\s*尾页", html)
        else:
            m = re.search(r'href="[^"]*/show/[^"]*?-{2,}(\d+)-{3,}\.html"[^>]*>\s*尾页', html)
        if m:
            try:
                return max(1, int(m.group(1)))
            except Exception:
                pass
        # 兜底: 可见的 1/N 文本
        m = re.search(r'>(\d+)/(\d+)<', html)
        if m:
            try:
                return max(1, int(m.group(2)))
            except Exception:
                pass
        return 0

    # ───────────── 分类 / 筛选 ─────────────
    def _home_html(self):
        """首页 HTML 全进程只抓一次 —— 导航(_nav)与列表(homeVideoContent)共用,
        原来是分别各抓一次(每次 81KB), 白白浪费一半首页请求。"""
        ck = "home_html"
        v = self._cget(ck)
        if v is not None:
            return v
        try:
            html = self._http(self._get_host() + "/", timeout=15)
        except Exception:
            html = ""
        return self._cset(ck, html, 300)

    def _nav(self):
        ck = "nav"
        v = self._cget(ck)
        if v is not None:
            return v
        html = self._home_html()
        out, seen = [], set()
        for m in re.finditer(r'href="[^"]*/type/(\d+)\.html"[^>]*>\s*([^<]{1,12})\s*<', html or ""):
            tid, nm = m.group(1), self._strip_tags(m.group(2))
            if nm and tid not in seen and "收起" not in nm:
                seen.add(tid)
                out.append({"type_id": tid, "type_name": nm})
        if not out:
            out = list(FALLBACK_CLASS)
        return self._cset(ck, out, 300)

    def _filters(self, tid):
        """解析 /type/{tid}.html 的筛选区 → TVBox filters。
        方法: 与全空基线比对, 找出变化的段位 → 自动映射键名(不写死段位)。"""
        ck = "filter_" + str(tid)
        v = self._cget(ck)
        if v is not None:
            return v
        try:
            html = self._http(self._get_host() + "/type/%s.html" % tid, timeout=15)
        except Exception:
            return self._cset(ck, {}, 60)
        html = self._clean(html)
        res = []
        # 每个筛选组一个 <ul class="myui-screen__list...">
        for um in re.finditer(r'<ul class="myui-screen__list[^"]*"[^>]*>([\s\S]*?)</ul>', html):
            block = um.group(1)
            lis = re.findall(r'<li[^>]*>([\s\S]*?)</li>', block)
            gname, values = "", []
            for li in lis:
                am = re.search(r"<a\b([^>]*)>([\s\S]*?)</a>", li)
                if not am:
                    continue
                hm = re.search(r'href="([^"]*)"', am.group(1))
                href = hm.group(1) if hm else ""
                label = self._strip_tags(am.group(2))
                if not href:                       # 无 href → 组名
                    if not gname:
                        gname = label
                    continue
                if not label or label == "全部":
                    continue
                sm = re.search(r'/show/([^.]+)\.html', href)
                if not sm:
                    continue
                segs = sm.group(1).split("-")
                base = ([str(tid)] + [""] * 11)
                diffs = [i for i in range(min(len(segs), 12))
                         if segs[i] and segs[i] != base[i]]
                if not diffs:
                    continue
                i = diffs[0]
                if i == 0:                          # 段1 变了 = 换子分类 tid
                    key, val = "sub", segs[0]
                else:
                    key, val = SEG_KEY.get(i, "p%d" % i), unquote(segs[i])
                if not any(x["n"] == label and x["v"] == val for x in values):
                    values.append({"n": label, "v": val})
            if gname and values:
                res.append({"key": self._key_for(gname, res), "name": gname, "value": values})
        return self._cset(ck, res, 1800)

    @staticmethod
    def _key_for(gname, existing):
        """组名 → 稳定英文键（中文组名当 key 部分壳子不认）"""
        m = {"类型": "sub", "剧情": "class", "地区": "area", "排序": "by",
             "年份": "year", "语言": "lang", "字母": "letter"}
        key = m.get(gname)
        if not key:
            key = "g%d" % (len(existing) + 1)
        base, n = key, 1
        while any(x["key"] == key for x in existing):
            n += 1
            key = "%s%d" % (base, n)
        return key

    def _show_url(self, tid, pg, extend=None):
        """按 12 段位拼 /show/ URL（段9=页码, 段12=年份, 其余按 extend 覆盖）"""
        segs = [""] * 12
        segs[0] = str(tid)
        segs[8] = str(max(1, int(pg or 1)))
        if isinstance(extend, dict):
            for k, val in extend.items():
                if val in (None, "", "全部"):
                    continue
                if k == "sub":
                    segs[0] = str(val)
                    continue
                idx = KEY_SEG.get(k)
                if idx is None and str(k).startswith("p") and str(k)[1:].isdigit():
                    idx = int(str(k)[1:])
                if idx is not None and 0 <= idx < 12:
                    segs[idx] = str(val)
        # 段值统一百分号编码（地区/剧情是中文, urllib 裸抓不认非 ASCII URL）
        segs = [quote(str(x), safe="") if x else "" for x in segs]
        return self._get_host() + "/show/" + "-".join(segs) + ".html"

    # ───────────── 六接口 ─────────────
    def homeContent(self, filter=False):
        self._warm_api()
        data = {"list": []}
        try:
            data["class"] = self._nav()
        except Exception:
            data["class"] = list(FALLBACK_CLASS)
        if filter:
            flt = {}
            # ★筛选页原来 5 个分类串行抓(每个 65KB), 改并行 → 一轮网络往返拿全部
            tids = [c["type_id"] for c in data["class"]]
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(5, max(1, len(tids)))) as ex:
                    futs = [(t, ex.submit(self._filters, t)) for t in tids]
                    for t, fu in futs:
                        try:
                            f = fu.result(timeout=30)
                        except Exception:
                            f = None
                        if f:
                            flt[t] = f
            except Exception:
                for t in tids:
                    try:
                        f = self._filters(t)
                        if f:
                            flt[t] = f
                    except Exception:
                        pass
            if flt:
                data["filters"] = flt
        try:
            data["list"] = self.homeVideoContent().get("list", [])
        except Exception:
            pass
        return data

    def homeVideoContent(self):
        ck = "home"
        v = self._cget(ck)
        if v is not None:
            return {"list": v}
        items = self._cards(self._home_html())
        return {"list": self._cset("home", items, 300)}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        self._warm_api()
        pg = int(pg or 1)
        url = self._show_url(tid, pg, extend)
        html = ""
        try:
            html = self._http(url, referer=self._get_host() + "/type/%s.html" % tid, timeout=18)
        except Exception:
            pass
        items = self._cards(html) if html else []
        pc = self._pagecount(html, "show") if html else 0
        if not items:                       # ★空页刹车: 不许无限空翻
            pc = pc or pg
        return {
            "page": pg,
            "pagecount": max(1, pc or pg),
            "limit": 36,
            "total": max(1, (pc or pg)) * 36,
            "list": items,
        }

    def searchContent(self, key, quick, pg="1"):
        self._warm_api()
        pg = int(pg or 1)
        kw = quote(str(key or "").strip())
        # 14 段位: 段1=关键词, 段11=页码
        url = self._get_host() + "/search/" + kw + "-" * 10 + str(pg) + "-" * 3 + ".html"
        html = ""
        try:
            html = self._http(url, referer=self._get_host() + "/", timeout=18)
        except Exception:
            pass
        items = self._cards(html) if html else []
        pc = self._pagecount(html, "search") if html else 0
        if not items:
            pc = pc or pg
        return {
            "page": pg,
            "pagecount": max(1, pc or pg),
            "limit": 36,
            "total": max(1, (pc or pg)) * 36,
            "list": items,
        }

    def detailContent(self, ids):
        self._warm_api()                          # ★进详情就预热, 用户看完简介点播时连接已就绪
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        out = []
        for vid in ids:
            vid = str(vid).strip()
            if not vid:
                continue
            try:
                v = self._detail_one(vid)
            except Exception as e:
                # ★详情抓不到绝不能抛(抛了整个源就崩) → 回一个带原因的空壳卡, 好定位现象
                v = {
                    "vod_id": vid,
                    "vod_name": "详情加载失败",
                    "vod_pic": "",
                    "vod_remarks": "[诊断] " + ("%s: %s" % (type(e).__name__, str(e)))[:80],
                    "vod_content": "站点响应异常，请稍后重试；若持续出现请反馈这行诊断文字。",
                    "vod_play_from": "",
                    "vod_play_url": "",
                }
            if v:
                out.append(v)
        return {"list": out}

    def _detail_one(self, vid):
        url = self._get_host() + "/detail/%s.html" % vid
        ck = "detail_" + vid
        hit = self._cget(ck)
        if hit is not None:
            return hit
        html = self._http(url, referer=self._get_host() + "/", timeout=20)
        raw = self._clean(html)

        # 片名
        m = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>([\s\S]*?)</h1>', raw)
        name = self._strip_tags(m.group(1)) if m else ""
        if not name:
            m = re.search(r'<title>\s*([^<-]+)', raw)
            name = self._strip_tags(m.group(1)) if m else vid
        # 封面
        pic = ""
        m = re.search(r'<a[^>]+href="[^"]*/play/[^"]+"[^>]*title="[^"]*"[^>]*>', raw)
        if m:
            for attr in ("data-original", "data-src", "data-lazy-src", "src"):
                pm = re.search(attr + r'="([^"]+)"', m.group(0))
                if pm and "load.png" not in pm.group(1):
                    pic = pm.group(1)
                    break
        if not pic:
            m = re.search(r'<img[^>]+(?:data-original|src)="([^"]+)"', raw)
            if m and "load.png" not in m.group(1):
                pic = m.group(1)
        if pic:
            pic = pic.replace("&amp;", "&")
            if pic.startswith("//"):
                pic = "https:" + pic
            elif pic.startswith("/"):
                pic = urljoin(self._get_host() + "/", pic)

        def field(label):
            mm = re.search(r'>' + label + r'：</span>([\s\S]*?)(?:</p>|<span class="split-line)', raw)
            return self._strip_tags(mm.group(1)) if mm else ""

        director = field("导演")
        actor = field("主演")
        area = field("地区")
        lang = field("语言")
        year = field("年份")
        cls = field("分类")
        alias = field("又名")

        # 评分 → 备注
        sm = re.search(r'data-score="([\d.]+)"', raw)
        score = sm.group(1) if sm else ""
        remarks = (score + "分") if score and float(score or 0) > 0 else ""
        um = re.search(r'更新时间：</span>\s*<span[^>]*>([\d\-: ]+)', raw)
        upd = um.group(1).strip() if um else ""
        if not remarks and upd:
            remarks = upd[:10]

        # 简介
        desc = ""
        dm = re.search(r'剧情简介：</span>([\s\S]*?)(?:<div class="myui-panel|<p class="text-muted col-pd">\s*<span class="text-muted">\s*演员)', raw)
        if not dm:
            dm = re.search(r'剧情简介：</span>([\s\S]{0,4000}?)</p>', raw)
        if dm:
            desc = self._strip_tags(dm.group(1))
            if "短评" in desc:
                desc = desc.split("短评", 1)[0]
            desc = desc.strip(" ，,：:")
            desc = re.sub(r"^《[^》]*》", "", desc)
            desc = re.sub(r"^豆瓣\s*[\d.]+\s*分\s*[，,]?", "", desc)

        # ── 线路与分集 ──
        tabs = re.findall(r'<a href="#playlist(\d+)" data-toggle="tab">([^<]+)</a>', raw)
        label_map = dict((i, self._strip_tags(t)) for i, t in tabs)
        blocks = re.split(r'<div id="playlist(\d+)"', raw)
        # blocks = [前文, id0, html0, id1, html1, ...]
        lines_from, lines_url = [], []
        for i in range(1, len(blocks) - 1, 2):
            pid = blocks[i]
            blk = blocks[i + 1]
            label = label_map.get(pid) or ("线路" + str(pid))
            eps = []
            for em in re.finditer(r'href="([^"]*/play/\d+-\d+-\d+\.html)"[^>]*>([^<]*)</a>', blk):
                p = em.group(1)
                if p.startswith("http"):
                    p = p.split(".xyz", 1)[-1] if ".xyz" in p else p
                if "/play/" not in p:
                    continue
                p = p[p.find("/play/"):]
                nm = self._norm_name(self._strip_tags(em.group(2))) or ("第%s集" % (len(eps) + 1))
                eps.append((nm, p))
            if not eps:
                continue
            # 去重（集名铁律: 同线路内不可重名）
            seen, uniq = set(), []
            for nm, p in eps:
                if nm in seen:
                    k = 2
                    while ("%s(%d)" % (nm, k)) in seen:
                        k += 1
                    nm = "%s(%d)" % (nm, k)
                seen.add(nm)
                uniq.append((nm, p))
            eps = uniq
            if len(eps) == 1 and eps[0][0].isdigit():
                eps = [("正片", eps[0][1])]          # 电影单集显示「正片」更好看
            is_pan = ("网盘" in label) or label in ("夸克", "百度", "UC")
            lines_from.append(label)
            lines_url.append((is_pan, eps))

        # 网盘真链（详情页「视频下载」区明文）
        pan_links = {}
        for pm2 in re.finditer(r'<b>\s*([^：:]{1,8})\s*[：:]\s*</b>\s*<a[^>]*href="(https?://[^"]+)"', raw):
            who, lnk = pm2.group(1).strip(), pm2.group(2)
            if "quark" in lnk or "夸" in who:
                pan_links["夸克"] = lnk
            elif "baidu" in lnk or "百" in who:
                pan_links["百度"] = lnk
            elif "uc" in lnk.lower() or "UC" in who.upper():
                pan_links["UC"] = lnk

        play_from, play_url = [], []
        for label, (is_pan, eps) in zip(lines_from, lines_url):
            if is_pan:
                lnk = ""
                for k, v2 in pan_links.items():
                    if k in label or (k == "UC" and "uc" in label.lower()):
                        lnk = v2
                        break
                if not lnk and pan_links:
                    lnk = list(pan_links.values())[0]
                if not lnk:
                    continue                      # 没有真链就不硬凑线路
                play_from.append(label)
                play_url.append("合集$pan@@@" + lnk)
            else:
                segs = []
                for nm, p in eps:
                    segs.append(nm + "$main@@@" + p)
                play_from.append(label)
                play_url.append("#".join(segs))

        # 兜底线: 网页嗅探（解密链哪天变了还能靠宿主内建嗅探顶上）
        first_play = ""
        for _, eps in lines_url:
            if eps:
                first_play = eps[0][1]
                break
        if first_play:
            play_from.append("网页嗅探")
            play_url.append("正片$sniff@@@" + first_play)
            play_from.append("宿主解析")
            play_url.append("正片$parse@@@" + first_play)

        vod = {
            "vod_id": vid,
            "vod_name": self._norm_name(name),
            "vod_pic": pic,
            "vod_remarks": self._norm_name(remarks),
            "vod_year": year,
            "vod_area": area,
            "vod_lang": lang,
            "vod_actor": self._norm_name(actor),
            "vod_director": self._norm_name(director),
            "vod_class": self._norm_name(cls),
            "vod_content": self._norm_name(desc)[:800],
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        if alias:
            vod["vod_en"] = self._norm_name(alias)
        # 后台预解析各视频线首集（点播时大概率已缓存好 → 秒开）
        pf = []
        for label, (is_pan, eps) in zip(lines_from, lines_url):
            if not is_pan and eps:
                pf.append("main@@@" + eps[0][1])
        try:
            self._prefetch(pf, limit=1)           # 只预热第一条线路首集(后台请求减半)
        except Exception:
            pass
        return self._cset(ck, vod, 600)

    # ───────────── 播放（核心 4 步解密链）─────────────
    def _player_aaaa(self, html):
        i = html.find("var player_aaaa=")
        if i < 0:
            i = html.find("var player_aaaa =")
        if i < 0:
            return {}
        j = html.find("{", i)
        blob = self._balanced(html, j) if j >= 0 else None
        if not blob:
            return {}
        try:
            return json.loads(blob)
        except Exception:
            return {}

    @staticmethod
    def _const(html, cname):
        m = re.search(r'const\s+' + re.escape(cname) + r'\s*=\s*"((?:[^"\\]|\\.)*)"', html or "")
        if not m:
            m = re.search(r'const\s+' + re.escape(cname) + r"\s*=\s*'((?:[^'\\]|\\.)*)'", html or "")
        if not m:
            return ""
        s = m.group(1)
        try:
            return json.loads('"' + s.replace('\\"', '"') + '"')
        except Exception:
            return s

    def _resolve(self, play_path):
        """★四步取真链: 播放页 → artplayer 常量 → API → AES 解密"""
        host = self._get_host()
        # 结果缓存（CDN 直链带 7 天签名, 缓 6 小时安全, 避免每次点播都等 API 10 秒+）
        ck_url = "url_" + hashlib.md5(play_path.encode("utf-8")).hexdigest()
        hit = self._cget(ck_url)
        if hit:
            return hit
        # ★先查 artplayer 常量缓存: 命中就完全跳过 ①播放页 ②播放器页 两次抓取(363KB)
        ck = "art_" + hashlib.md5(play_path.encode("utf-8")).hexdigest()
        consts = self._cget(ck)
        if consts is None:
            # ① 播放页抠 player_aaaa
            html = self._http(host + play_path, referer=host + "/", timeout=20)
            pa = self._player_aaaa(html)
            hexurl = str(pa.get("url") or "")
            if not hexurl:
                raise RuntimeError("player_aaaa.url 为空")
            # ② artplayer 页常量（363KB 页面, 按 play_path 缓存 600 秒）
            art = self._http(host + "/static/player/artplayer/?url=" + hexurl,
                             referer=host + play_path, timeout=25)
            consts = {
                "vkey": self._const(art, "playPageUrl"),
                "ts": self._const(art, "timestamp"),
                "code": self._const(art, "secretKeySeed") or "",
            }
            if not consts["vkey"] or not consts["ts"]:
                raise RuntimeError("artplayer 常量缺失")
            self._cset(ck, consts, 600)
        # ③ 调 API 换密文（★t 只有约 60 秒时效 → 用 callable, 让每次重试都现算新 t+签名）
        self._wait_warm()                         # 预热快好就等它, 直接复用现成连接(省一次冷握手)
        def _payload():
            t = int(time.time())
            return {
                "vkey": consts["vkey"],
                "code": consts["code"],
                "t": t,
                "signature": hashlib.md5(str(t).encode("utf-8")).hexdigest(),
            }
        status, body = self._post_json(API_URL, _payload, origin=host, timeout=35)
        if status != 200:
            raise RuntimeError("API %s" % status)
        try:
            j = json.loads(body)
        except Exception:
            raise RuntimeError("API 非 JSON")
        if j.get("code") != 200 or not j.get("url"):
            raise RuntimeError("API 业务失败: %s %s" % (
                j.get("code"), (j.get("msg") or j.get("error") or "")[:50]))
        # ④ AES-128-CBC 解密（iv 在前 key 在后）
        md5h = hashlib.md5((str(consts["ts"]) + SALT).encode("utf-8")).hexdigest()
        key = md5h[16:32].encode("utf-8")
        iv = md5h[:16].encode("utf-8")
        raw = base64.b64decode(str(j["url"]))
        if len(raw) % 16:
            raise RuntimeError("密文长度异常")
        plain = aes_cbc_decrypt(raw, key, iv).decode("utf-8", "ignore").strip()
        if not plain.startswith("http"):
            raise RuntimeError("解密结果非法")
        self._cset(ck_url, plain, 3600 * 6)
        return plain

    def _prefetch(self, pids, limit=3):
        """后台预解析（不阻塞详情返回）。
        背景: 该站取链 API 服务端要 10 秒+, 用户点播会干等 → 详情一打开就先热几条, 点播即出。"""
        if not pids or self._cget("pf_busy"):
            return
        self._cset("pf_busy", 1, 60)

        def _work():
            for p in pids[:limit]:
                try:
                    self._resolve(p)
                except Exception:
                    pass

        try:
            import threading
            th = threading.Thread(target=_work, name="dida-prefetch")
            th.daemon = True
            th.start()
        except Exception:
            self._cset("pf_busy", 0, 0)

    def playerContent(self, flag, id, vipFlags):
        pid = str(id or "")
        mode, path = "main", pid
        if "@@@" in pid:
            mode, path = pid.split("@@@", 1)
        host = self._get_host()

        # 网盘分享线
        if mode == "pan":
            return {"parse": 1, "url": path, "header": {"User-Agent": self._ua}}

        # 网页嗅探线: 回原播放页交宿主内建嗅探
        if mode == "sniff":
            return {"parse": 0, "url": host + path, "header": {"User-Agent": self._ua}}

        # 宿主解析线: 交外部解析接口
        if mode == "parse":
            return {"parse": 1, "url": host + path, "header": {"User-Agent": self._ua}}

        # 主线路: 四步解密 → 直链
        try:
            real = self._resolve(path)
            hdr = {"User-Agent": self._ua}
            if self.isVideoFormat(real):
                return {"parse": 0, "url": real, "header": hdr, "format": "m3u8" if ".m3u8" in real else ""}
            return {"parse": 0, "url": real, "header": hdr}
        except Exception as e:
            # 解密链失败 → 回落 parse:1 交宿主解析（绝不返回空让它白屏）
            try:
                return {"parse": 1, "url": host + path,
                        "header": {"User-Agent": self._ua}, "msg": str(e)[:60]}
            except Exception:
                return {"parse": 0, "url": "", "header": {}}

    def localProxy(self, param=None):
        """四元组契约 [status, mime, bytes, header]（本源不产生 proxy:// 地址）"""
        return [404, "text/plain; charset=utf-8", b"not found", {}]
