# -*- coding: utf-8 -*-
"""
注视影视 gaze.red — TVBox / FongMi type=3 源（Chaquopy Python）  v2

v2 针对 andy 反馈「不开代理打不开 + 开代理也慢」的两处修法：
① 打不开 = 国内 DNS 污染/被墙 → 内置镜像域名池自动切换：
   gaze.red -> gazes.top -> gazes.store -> gaze.show -> gazes.host（站方公告里已污染的
   gaze.run/gaze.host/gazes.site 不放），DNS 报错/超时/403/5xx 立刻换下一个，换到能通的就记住；
   extend 仍可强制指定 host（{"host":"gazes.top"}）。
② 慢 = a) 请求开 gzip（首页 230KB→约 40KB、详情 320KB→约 70KB）
        b) 有 requests 就用（连接复用/自动解压），没有才退回 urllib+手动解压
        c) 节流 0.45s→0.15s；首页缓存 60s→300s、分类页 proof 缓存 90s→600s（419 自动换新）
        d) 每请求超时收紧，失败快速换域名不空等

站点说明书（逆向结论，2026-09-28 实测）：
1) 站型：自写 PHP 模板站（Cloudflare + PHPSESSID），无 MacCMS 采集接口（/api.php/provide/vod 404）。
2) 首页 / 是服务端直出 HTML（186 张卡，article + href="play/{32hex}"），可直抓不需验证。
3) /filter（分类/搜索壳）与 /play/{mid} 被「Cap 验证」拦：Cap = 工作量证明(PoW)人机验证，
   协议已完全逆向：
     POST /event/cap/challenge -> {challenge:{c,s,d}, token}
     salt = y(token+i, s), target = y(token+i+'d', d)   # y = FNV1a变体 + xorshift32 出 hex
     解 c 个 SHA-256(salt+nonce) 前缀命中 target 的 nonce
     POST /event/cap/redeem {token, solutions} -> Set-Cookie __Host-vs（3 天有效）
   覆盖 cookie 后任意页面直出，无需再点验证。
4) 列表/搜索统一走 POST /filter_movielist，body=mform&mcountry&genre_arr&page&sort&album&title&years，
   返回 {code,pages,mlist:[{id,title,grade,cover_img,mid}]}；但要带「页面 proof 头」否则 419。
   proof 头 = 页面里那段混淆 JS：用内嵌 data-URI 的 10x10 BMP 指纹图 + 每页随机 base64 常量
   算出 {头名:头值}（filter 页 1 个 + 随机 DOM 头 x-gaze-*；play 页 3 个），本文件已 100% 移植成 Python。
5) 详情/播放页 /play/{mid}：片名 h1、封面 og:image、简介 <p class="ev">、标签 a.dF、
   分组 tab（默认/dytt…）、集数 = .playbtn[data-id|data-path]（同一页里选集，无 URL 深链接）。
6) 播放地址 data-src 是 hex 密文，由 Go-WASM（藏在 ctyun CDN 的 svg+文件里）在浏览器端解密，
   Python 侧拿不到明文 -> 播放交 App 内建嗅探（parse=0 + Cookie 头，免去二次验证）。
"""
import base64
import gzip
import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html import unescape
from http.cookiejar import CookieJar

try:
    import requests as _requests          # 有就用：连接复用 + 自动解压（更快）
    HAS_REQUESTS = True
except Exception:
    _requests = None
    HAS_REQUESTS = False

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self, extend=""):
            pass


SITE = "https://gaze.red"
# 镜像池：站方公告确认 gaze.run/gaze.host/gazes.site 已 DNS 污染故不放；顺序即尝试顺序
MIRRORS = ["gaze.red", "gazes.top", "gazes.store", "gaze.show", "gazes.host"]
_GOOD_HOST = None          # 本进程内最近一次能通的镜像，下次直接从它开始
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHALLENGE_MARK = "<cap-widget data-cap-api-endpoint"

CLASSES = [
    {"type_id": "all", "type_name": "全部影视"},
    {"type_id": "1", "type_name": "电影"},
    {"type_id": "2", "type_name": "电视剧"},
    {"type_id": "bangumi", "type_name": "番剧"},
    {"type_id": "chinese_cartoon", "type_name": "国漫"},
]

COUNTRIES = [("all", "全部地区"), ("1", "中国大陆"), ("2", "中国台湾"), ("3", "中国香港"),
             ("4", "韩国"), ("5", "俄罗斯"), ("6", "美国"), ("7", "日本"), ("8", "印度"),
             ("9", "英国"), ("10", "德国"), ("11", "法国"), ("12", "意大利"), ("13", "泰国"),
             ("14", "爱沙尼亚"), ("15", "哈萨克斯坦"), ("16", "西班牙"), ("17", "黎巴嫩"),
             ("18", "巴西"), ("19", "澳大利亚"), ("20", "丹麦"), ("21", "瑞典"), ("22", "以色列"),
             ("23", "荷兰"), ("24", "伊朗"), ("25", "墨西哥"), ("26", "奥地利"), ("27", "智利"),
             ("28", "马来西亚"), ("29", "哥伦比亚"), ("30", "挪威"), ("31", "爱尔兰"),
             ("32", "罗马尼亚"), ("33", "比利时"), ("34", "瑞士"), ("35", "加拿大"), ("36", "波兰")]

GENRES = [("all", "全部类型"), ("1", "剧情"), ("2", "动作"), ("3", "喜剧"), ("4", "爱情"),
          ("5", "科幻"), ("6", "悬疑"), ("7", "惊悚"), ("8", "恐怖"), ("9", "犯罪"),
          ("10", "音乐"), ("11", "冒险"), ("12", "历史"), ("13", "战争"), ("14", "奇幻"),
          ("15", "黑帮"), ("16", "文艺"), ("17", "传记"), ("18", "运动"), ("19", "同性"),
          ("20", "情色")]

SORTS = [("default", "默认排序"), ("grade", "评分排序"), ("name", "名称排序"),
         ("createtime", "添加时间排序"), ("updatetime", "修改时间排序")]

YEARS = [("all", "全部年份")] + [(str(y), str(y)) for y in range(2026, 2009, -1)]


# ============================ Cap(PoW) 验证 ============================
def _toi32(x):
    x %= 1 << 32
    return x - (1 << 32) if x >= (1 << 31) else x


def _y(seed, length):
    """cap.js 的 y(str,len)：FNV1a 变体 + xorshift32，严格 JS 语义（已与 JS 对拍）"""
    n = 2166136261
    for ch in seed:
        n = _toi32(n ^ ord(ch))
        s = (_toi32(n << 1) + _toi32(n << 4) + _toi32(n << 7)
             + _toi32(n << 8) + _toi32(n << 24))
        n = n + s
    n = _toi32(n) & 0xFFFFFFFF
    out = ""
    while len(out) < length:
        n = _toi32(n ^ _toi32(n << 13))
        n = _toi32(n ^ _toi32((n & 0xFFFFFFFF) >> 17))
        n = _toi32(n ^ _toi32(n << 5))
        out += format(n & 0xFFFFFFFF, "08x")
    return out[:length]


def _pow_solve(salt, target):
    want = bytes.fromhex(target)
    n = len(want)
    i = 0
    while True:
        if hashlib.sha256((salt + str(i)).encode("utf-8")).digest()[:n] == want:
            return i
        i += 1


# ============================ 页面 proof 头 ============================
def _bmp_rgba(raw):
    off = int.from_bytes(raw[10:14], "little")
    w = int.from_bytes(raw[18:22], "little", signed=True)
    h = int.from_bytes(raw[22:26], "little", signed=True)
    bc = int.from_bytes(raw[28:30], "little")
    if bc != 24 or w <= 0 or h == 0:
        raise RuntimeError("bmp unsupported")
    bottom_up = h > 0
    hh = abs(h)
    row = (w * bc + 31) // 32 * 4
    out = []
    for y in range(hh):
        sy = (hh - 1 - y) if bottom_up else y
        p = off + sy * row
        for _ in range(w):
            b, g, r = raw[p], raw[p + 1], raw[p + 2]
            p += 3
            out += [r, g, b, 255]
    return out


def _stages(script):
    decls = list(re.finditer(r"Uint8Array\.from\(atob\('([A-Za-z0-9+/=]+)'\)", script))
    if not decls or len(decls) % 2:
        raise RuntimeError("bad stages")
    res = []
    for i in range(0, len(decls), 2):
        k, m = decls[i], decls[i + 1]
        end = decls[i + 2].start() if i + 2 < len(decls) else len(script)
        cs = script.rfind("const", 0, k.start())
        text = script[cs:end] if cs != -1 and k.start() - cs < 40 else script[k.start():end]
        res.append((k.group(1), m.group(1), text))
    return res


def _proof_from_html(html):
    """整页 HTML -> 该页 proof 请求头（含随机 DOM 头）"""
    m = re.search(r'<img id="[^"]+" src="data:image/[^;]+;base64,([A-Za-z0-9+/=]+)"', html)
    if not m:
        raise RuntimeError("fingerprint img missing")
    pix = _bmp_rgba(base64.b64decode(m.group(1)))
    blocks = re.findall(r'<script type="[^"]*text/javascript">(.*?)</script>', html, re.S)
    blk = [b for b in blocks if "getImageData" in b]
    if not blk:
        raise RuntimeError("proof script missing")
    full = blk[0]
    script = full[:full.find("(() => {")] if "(() => {" in full else full

    mv = re.search(r"getImageData\(0,\s*0,\s*10,\s*10\)\.data", script)
    if not mv:
        raise RuntimeError("getImageData missing")
    pre = script.rfind("const", 0, mv.start())
    nm = re.search(r"const\s+(\w+)\s*=", script[pre:mv.start() + 10] if pre != -1 else script)
    pixvar = nm.group(1) if nm else ""

    ops = []
    for k64, m64, text in _stages(script):
        cm = re.search(r"\^\(\(\((\d+)\^\s*(\d+)\)\+\s*\w+\s*\*\s*(\d+)\)&(\d+)\)", text)
        if not cm:
            raise RuntimeError("const missing")
        ops.append({
            "k": list(base64.b64decode(k64)),
            "m": list(base64.b64decode(m64)),
            "c": int(cm.group(1)) ^ int(cm.group(2)),
            "mul": int(cm.group(3)),
            "mask": int(cm.group(4)),
            "kind": ("PIX" if pixvar and (pixvar + "[") in text
                     else "FINAL" if re.search(r"\d+\s*\*\s*16", text)
                     and "Array.from({length" in text.replace(" ", "") else "PICK"),
            "chunks": int(re.search(r"(\d+)\s*\*\s*16", text).group(1))
            if re.search(r"\d+\s*\*\s*16", text) and "Array.from({length" in text.replace(" ", "") else 0,
        })
    kinds = [o["kind"] for o in ops]
    if kinds != ["PIX", "PICK"] * ((len(ops) - 1) // 2) + ["FINAL"]:
        raise RuntimeError("layout %s" % kinds)

    picks, cur, final = [], None, None
    for op in ops:
        K, M, c, mul, mask = op["k"], op["m"], op["c"], op["mul"], op["mask"]
        if op["kind"] == "PIX":
            out, j = [], 0
            for i in range(0, len(K), 2):
                v = ((K[i] ^ M[i]) | ((K[i + 1] ^ M[i + 1]) << 8)) ^ ((c + j * mul) & mask)
                idx = (((v >> 4) & 15) * 10 + (v & 15)) * 4 + ((v >> 8) & 3)
                out.append(pix[idx] if 0 <= idx < len(pix) else None)
                j += 1
            cur = out
        elif op["kind"] == "PICK":
            out = []
            for i in range(len(K)):
                idx = (K[i] ^ M[i]) ^ ((c + i * mul) & mask)
                out.append(cur[idx] if 0 <= idx < len(cur) else None)
            cur = out
            picks.append(out)
        else:
            n = op["chunks"] * 16
            arr = []
            for j in range(n):
                v = ((K[2 * j] ^ M[2 * j]) | ((K[2 * j + 1] ^ M[2 * j + 1]) << 8)) ^ ((c + j * mul) & mask)
                src = picks[v & 7] if (v & 7) < len(picks) else None
                off = v >> 3
                arr.append(src[off] if src is not None and 0 <= off < len(src) else None)
            if any(b is None for b in arr):
                raise RuntimeError("assembly undefined")
            final = ["".join("%02x" % b for b in arr[i * 16:(i + 1) * 16])
                     for i in range(op["chunks"])]
    if not final:
        raise RuntimeError("no final")

    def ev(expr):
        mm = re.search(r"(\d+)\s*\^\s*(\d+)", expr)
        if mm:
            return int(mm.group(1)) ^ int(mm.group(2))
        return int(expr.strip())

    hdrs = {}
    pat = (r"(\w+)\[([^\]]+)\]\s*=\s*\{\s*\[\s*(\w+)\[([^\]]+)\]\s*\]\s*:"
           r"\s*(\w+)\[([^\]]+)\]\s*\}")
    for mm in re.finditer(pat, script):
        try:
            a, b = ev(mm.group(4)), ev(mm.group(6))
        except Exception:
            continue
        if 0 <= a < len(final) and 0 <= b < len(final):
            hdrs[final[a]] = final[b]
    if not hdrs:
        hdrs = {final[0]: final[1]} if len(final) >= 2 else {}

    ah = re.search(r'randomDomHeader\s*=\s*"([^"]+)"', full)
    aa = re.search(r'randomDomAttr\s*=\s*"([^"]+)"', full)
    if ah and aa:
        sp = re.search(r"\s" + re.escape(aa.group(1)) + r'="([0-9a-f]{16,})"', html)
        if sp:
            hdrs[ah.group(1)] = sp.group(1)
    return hdrs


# ============================ 会话 ============================
class Session(object):
    """统一网络入口：镜像池自动切换 / Cap 自动过 / proof 自动算 / gzip 压缩 / 419 429 自愈"""

    def __init__(self, host=""):
        global _GOOD_HOST
        self.hosts = []
        for h in MIRRORS:
            if h not in self.hosts:
                self.hosts.append(h)
        if _GOOD_HOST:                      # 记住上次通的镜像，省掉撞死域名的等待
            if _GOOD_HOST in self.hosts:
                self.hosts.remove(_GOOD_HOST)
            self.hosts.insert(0, _GOOD_HOST)
        self.host = "https://" + self.hosts[0]
        if host:
            h = host if host.startswith("http") else "https://" + host
            h = h.rstrip("/")
            dom = h.split("//", 1)[-1].split("/", 1)[0]
            if dom in self.hosts:
                self.hosts.remove(dom)
            self.hosts.insert(0, dom)
            self.host = h
        self.ok_host = self.host          # 最近一次成功的镜像
        self.last_err = ""

        if HAS_REQUESTS:
            self.rs = _requests.Session()
            self.rs.headers.update({"User-Agent": UA})
            self.cj = None
            self.opener = None
        else:
            self.rs = None
            self.cj = CookieJar()
            self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

        self._filter_html = ""
        self._filter_ts = 0
        self._home_html = ""
        self._home_ts = 0
        self._cap_ts = 0
        self._last_req = 0.0

    # ---- 节流（防触发频率风控，但不能拖慢） ----
    def _throttle(self, gap=0.15):
        dt = time.time() - self._last_req
        if dt < gap:
            time.sleep(gap - dt)

    # ---- cookie ----
    def cookie_header(self):
        if self.rs is not None:
            try:
                return "; ".join("%s=%s" % (k, v) for k, v in self.rs.cookies.get_dict().items())
            except Exception:
                return ""
        return "; ".join("%s=%s" % (c.name, c.value) for c in (self.cj or []))

    def has_cap(self):
        if self.rs is not None:
            try:
                return "__Host-vs" in self.rs.cookies.get_dict()
            except Exception:
                return False
        return any(c.name == "__Host-vs" for c in (self.cj or []))

    def _base_headers(self):
        return {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",      # ← 体积砍 4~5 倍，慢网络提速关键
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        }

    @staticmethod
    def _decompress(body, encoding):
        if not body:
            return body
        try:
            if encoding == "gzip":
                return gzip.decompress(body)
            if encoding == "deflate":
                try:
                    return zlib.decompress(body)
                except Exception:
                    return zlib.decompress(body, -zlib.MAX_WBITS)
        except Exception:
            return body
        return body

    def _rotate(self):
        """换镜像；返回是否还有下一个"""
        if len(self.hosts) < 2:
            return False
        cur = self.host.split("//", 1)[-1].split("/", 1)[0]
        if cur in self.hosts:
            i = self.hosts.index(cur)
            nxt = self.hosts[(i + 1) % len(self.hosts)]
        else:
            nxt = self.hosts[0]
        if nxt == cur:
            return False
        self.host = "https://" + nxt
        return True

    def _send(self, method, path, data=None, headers=None, timeout=15):
        """单次请求（当前镜像）。连不上/被封自动换镜像重试，全挂才抛错"""
        tries = max(1, len(self.hosts))
        last = ""
        for k in range(tries):
            url = self.host + path
            h = self._base_headers()
            if headers:
                h.update(headers)
            # gzip 交给 requests 自动处理；urllib 要手动
            if self.rs is None:
                pass
            else:
                h.pop("Accept-Encoding", None)      # requests 自带并自动解压
            try:
                self._throttle()
                if self.rs is not None:
                    r = self.rs.request(method, url, data=data, headers=h,
                                        timeout=timeout, allow_redirects=True)
                    self._last_req = time.time()
                    if r.status_code >= 400:
                        raise urllib.error.HTTPError(url, r.status_code, r.reason,
                                                     dict(r.headers), None)
                    self.ok_host = self.host
                    _GOOD_HOST = self.host.split("//")[-1].split("/", 1)[0]
                    self.last_err = ""
                    return r.content
                req = urllib.request.Request(url, data=data, headers=h, method=method)
                with self.opener.open(req, timeout=timeout) as resp:
                    raw = resp.read()
                    enc = (resp.headers.get("Content-Encoding") or "").lower().strip()
                    self._last_req = time.time()
                    self.ok_host = self.host
                    _GOOD_HOST = self.host.split("//")[-1].split("/", 1)[0]
                    self.last_err = ""
                    return self._decompress(raw, enc)
            except urllib.error.HTTPError as e:
                last = "HTTP %s @%s" % (e.code, self.host.split("//")[-1])
                self.last_err = last
                if e.code in (404, 419, 428, 429):     # 交给上层处理，不换域名
                    raise
                if e.code in (403, 503, 520, 521, 522, 523, 524):
                    if self._rotate():
                        continue
                    raise
                raise
            except Exception as e:
                # DNS 污染 / 连接失败 / 超时 → 立刻换下一个镜像
                last = "%s @%s" % (type(e).__name__, self.host.split("//")[-1])
                self.last_err = last
                if self._rotate():
                    continue
                raise
        raise RuntimeError(last or "no host")

    # ---- 高层 ----
    def get(self, path, referer=None, tries=3):
        """GET：Cap 挑战自动过 / 429 退避 / 域名自动换"""
        last = None
        deadline = time.time() + 26
        for i in range(tries):
            if time.time() > deadline:
                break
            try:
                if referer and referer.startswith("/"):
                    referer = self.host + referer
                hs = {"Referer": referer} if referer else None
                body = self._send("GET", path, headers=hs)
                if self._is_challenge(body):
                    last = "challenge"
                    self.last_err = "challenge"
                    self.cap_bypass()
                    continue
                return body
            except urllib.error.HTTPError as e:
                last = "HTTP %s" % e.code
                self.last_err = last
                if e.code in (419, 428):
                    self.cap_bypass()
                    continue
                if e.code == 429:
                    time.sleep(6)
                    continue
                if e.code == 403:
                    time.sleep(4)
                    continue
                return None
            except Exception as e:
                last = repr(e)[:90]
                self.last_err = last
                time.sleep(1.5)
        return None

    def post(self, path, data, referer=None, tries=3, extra_headers=None):
        payload = urllib.parse.urlencode(data).encode("utf-8")
        last = None
        deadline = time.time() + 26
        for i in range(tries):
            if time.time() > deadline:
                break
            try:
                hs = {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                      "Origin": self.host, "X-Requested-With": "XMLHttpRequest"}
                if referer:
                    hs["Referer"] = self.host + referer if referer.startswith("/") else referer
                if extra_headers:
                    hs.update(extra_headers)         # 页面 proof 头（缺了会 419）
                return self._send("POST", path, data=payload, headers=hs)
            except urllib.error.HTTPError as e:
                last = "HTTP %s" % e.code
                self.last_err = last
                if e.code in (419, 428):             # 419=proof 过期；428=验证过期
                    if e.code == 419:
                        raise ProofExpired(last)
                    self.cap_bypass()
                    continue
                if e.code == 429:
                    time.sleep(6)
                    continue
                if e.code == 403:
                    time.sleep(4)
                    continue
                return None
            except ProofExpired:
                raise
            except Exception as e:
                last = repr(e)[:90]
                self.last_err = last
                time.sleep(1.5)
        return None

    def _is_challenge(self, body):
        return body and CHALLENGE_MARK.encode("utf-8") in body[:8000]

    def cap_bypass(self):
        """过 Cap：challenge -> 算 PoW -> redeem -> 拿 __Host-vs（3 天有效）"""
        now = time.time()
        if self.has_cap() and now - self._cap_ts < 60:
            return True
        try:
            self._send("GET", "/", timeout=15)
        except Exception:
            pass
        try:
            raw = self._send("POST", "/event/cap/challenge", data=b"",
                             headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                      "Origin": self.host, "Referer": self.host + "/"},
                             timeout=15)
            j = json.loads(raw)
            ch = j["challenge"]
            tok = j["token"]
            sols = [_pow_solve(_y(tok + str(i), ch["s"]), _y(tok + str(i) + "d", ch["d"]))
                    for i in range(1, int(ch["c"]) + 1)]
            body = json.dumps({"token": tok, "solutions": sols}).encode("utf-8")
            raw = self._send("POST", "/event/cap/redeem", data=body,
                             headers={"Content-Type": "application/json",
                                      "Origin": self.host, "Referer": self.host + "/"},
                             timeout=15)
            out = json.loads(raw)
            if not out.get("success"):
                self.last_err = "cap redeem失败"
                return False
            self._cap_ts = time.time()
            return True
        except Exception as e:
            self.last_err = "cap:%s" % repr(e)[:60]
            return False

    def home_html(self, cache=300):
        """首页 HTML（gzip 后约 40KB），默认缓存 5 分钟"""
        if self._home_html and time.time() - self._home_ts < cache:
            return self._home_html
        body = self.get("/")
        if not body:
            return self._home_html or ""
        html = body.decode("utf-8", "replace")
        if CHALLENGE_MARK in html[:8000]:
            return self._home_html or ""
        self._home_html = html
        self._home_ts = time.time()
        return html

    def filter_html(self, force=False, cache=600):
        """分类/搜索页（proof 源头），默认缓存 10 分钟，419 时自动换新"""
        if not force and self._filter_html and time.time() - self._filter_ts < cache:
            return self._filter_html
        body = self.get("/filter", referer=self.host + "/")
        if not body:
            return self._filter_html or ""
        html = body.decode("utf-8", "replace")
        if CHALLENGE_MARK in html[:8000]:
            return self._filter_html or ""
        self._filter_html = html
        self._filter_ts = time.time()
        return html

    def movielist(self, params, tries=3):
        """POST /filter_movielist，proof 过期自动换新页重试"""
        for i in range(tries):
            html = self.filter_html(force=(i > 0))
            if not html:
                return None
            try:
                proof = _proof_from_html(html)     # 页面 proof 头
            except Exception as e:
                self.last_err = "proof:%s" % repr(e)[:60]
                self._filter_ts = 0
                continue
            try:
                raw = self.post("/filter_movielist", params,
                                referer="/filter", extra_headers=proof)
            except ProofExpired:
                self._filter_ts = 0
                time.sleep(1)
                continue
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return None
        return None


class ProofExpired(Exception):
    pass


# ============================ HTML 解析 ============================
def _clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


BAD_PIC = ("colorful.svg", "loading", "placeholder", "default", "blank", "transparent")


def _fix_pic(u, host=SITE):
    if not u:
        return ""
    u = u.split(" ")[0].strip()
    if u.startswith("/"):
        return host + u
    return u


def _home_cards(html, limit=0):
    out, seen = [], set()
    for part in html.split("<article")[1:]:
        mm = re.search(r'href="play/([0-9a-f]{32})"', part)
        if not mm:
            continue
        mid = mm.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        tm = re.search(r'aria-label="播放\s*([^"]+)"', part) or \
            re.search(r'<h3 class="aa"><span[^>]*>(.*?)</span>', part, re.S)
        title = _clean(tm.group(1)) if tm else ""
        cm = re.search(r'data-src="(https?://[^"]+)"', part) or \
            re.search(r'<img[^>]*\bsrc="(https?://[^"]+)"', part)
        pic = _fix_pic(cm.group(1)) if cm else ""
        badges = [_clean(x) for x in re.findall(r'class="bJ[^"]*">([^<]+)</div>', part)]
        remark = " / ".join([b for b in badges if b][:2])
        if not title:
            continue
        out.append({"vod_id": mid, "vod_name": title, "vod_pic": pic, "vod_remarks": remark})
        if limit and len(out) >= limit:
            break
    return out


def _detail_info(html):
    info = {}
    tm = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if tm:
        info["name"] = _clean(tm.group(1)).replace("在线播放", "").strip()
    if not info.get("name"):
        tm = re.search(r'var file_title = "([^"]+)"', html)
        info["name"] = _clean(tm.group(1)) if tm else ""
    cm = re.search(r'property="og:image"\s+content="([^"]+)"', html)
    if not cm:
        cm = re.search(r'var file_cover = "([^"]+)"', html)
    info["pic"] = _fix_pic(cm.group(1).replace("\\/", "/")) if cm else ""
    dm = re.search(r'property="og:description"\s+content="([^"]*)"', html)
    if not dm:
        dm = re.search(r'<meta name="description" content="([^"]*)"', html)
    info["desc"] = _clean(dm.group(1)) if dm else ""
    # 标签：豆瓣分 / 类型 / 地区 / 年份
    box = re.search(r'<div class="eV">(.*?)</div>\s*</div>', html, re.S)
    seg = box.group(1) if box else html
    grade = re.search(r'<h5 class="lc">\s*(豆瓣[^<]*)</h5>', seg)
    info["grade"] = _clean(grade.group(1)) if grade else ""
    tags = []
    for am in re.finditer(r'<a href="/filter\?[^"]*"[^>]*>([^<]+)</a>', seg):
        t = _clean(am.group(1))
        if t and t not in tags:
            tags.append(t)
    info["tags"] = tags
    return info


def _groups(html):
    """线路 tab：[{gid, name}]"""
    out = []
    for m in re.finditer(r'id="play-source-tab-g(\d+)"[^>]*data-source-group="(\d+)"[^>]*>(.*?)</button>',
                         html, re.S):
        name = _clean(m.group(3)) or ("线路" + m.group(2))
        out.append({"gid": m.group(2), "name": name})
    if not out:
        for m in re.finditer(r'data-source-group="(\d+)"', html):
            if m.group(1) not in [g["gid"] for g in out]:
                out.append({"gid": m.group(1), "name": "线路" + m.group(1)})
    return out


def _episodes(html):
    """默认线路集数：[{path, name, id}]"""
    out = []
    for m in re.finditer(
            r'<button type="button"\s+class="playbtn([^"]*)"\s+data-id="(\d+)"\s+data-mid="(\d+)"'
            r'\s+data-path="(\d+)"\s+data-src="[0-9a-f]+"[^>]*>\s*([^<]*?)\s*</button>', html):
        name = _clean(m.group(5)) or ("第%s集" % m.group(4))
        name = name.replace("$", "").replace("#", "")
        out.append({"path": int(m.group(4)), "name": name, "id": m.group(2)})
    if not out:
        for m in re.finditer(r'data-path="(\d+)"[^>]*>\s*([^<]{1,20}?)\s*</button>', html):
            name = _clean(m.group(2)).replace("$", "").replace("#", "")
            if name:
                out.append({"path": int(m.group(1)), "name": name, "id": ""})
    out.sort(key=lambda x: x["path"])
    return out


def _cookie_header(sess):
    return "; ".join("%s=%s" % (c.name, c.value) for c in sess.cj)


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

    # ---- 契约方法 ----
    def init(self, extend=""):
        self.extend = extend or ""
        self.sess = Session()
        host = ""
        if isinstance(extend, dict):
            host = str(extend.get("host", "") or "").strip()
        elif isinstance(extend, str) and extend.strip():
            s = extend.strip()
            if s.startswith("{"):
                try:
                    host = str(json.loads(s).get("host", "") or "").strip()
                except Exception:
                    host = ""
            elif "://" in s or "." in s:
                host = s
        if host:
            if not host.startswith("http"):
                host = "https://" + host
            self.sess = Session(host.rstrip("/"))
        return self

    def getDependence(self):
        return []

    def getName(self):
        return "注视影视"

    def isVideoFormat(self, url):
        return any(ext in str(url).lower()
                   for ext in [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg"])

    def manualVideoCheck(self):
        return False

    def _s(self):
        if self.sess is None:
            self.init(self.extend)
        return self.sess

    # ---- 首页 ----
    def homeContent(self, filter=False):
        s = self._s()
        try:
            html = s.home_html()
        except Exception:
            html = ""
        lst = _home_cards(html) if html else []
        if not lst:
            lst = [{"vod_id": "diag",
                    "vod_name": "[诊断] 首页抓取失败",
                    "vod_pic": "",
                    "vod_remarks": "反馈这行字给我",
                    "vod_content": "原因: %s ｜ 时间: %s ｜ 当前镜像: %s ｜ 已通镜像: %s"
                                   % (s.last_err or "未知", time.strftime("%H:%M:%S"),
                                      s.host, s.ok_host)}]
        return {"class": CLASSES, "list": lst, "filters": self._filters()}

    def homeVideoContent(self):
        s = self._s()
        try:
            html = s.home_html()
        except Exception:
            html = ""
        return {"list": _home_cards(html, limit=30) if html else []}

    def _filters(self):
        def mk(key, name, vals):
            return {"key": key, "name": name,
                    "value": [{"n": n, "v": v} for v, n in vals]}
        common = [mk("mcountry", "地区", COUNTRIES), mk("genre", "类型", GENRES),
                  mk("sort", "排序", SORTS), mk("years", "年份", YEARS)]
        return dict((c["type_id"], common) for c in CLASSES)

    # ---- 分类 / 搜索 ----
    def _list_from_resp(self, resp, pg):
        pg = int(pg) if str(pg).isdigit() else 1
        if not resp or resp.get("code") != 1:
            return {"page": pg, "pagecount": pg, "limit": 36, "total": 0, "list": []}
        items = []
        for it in (resp.get("mlist") or []):
            mid = str(it.get("mid") or "").strip()
            title = _clean(str(it.get("title") or ""))
            if not mid or not title:
                continue
            grade = str(it.get("grade") or "").strip()
            remark = ("豆瓣 " + grade) if grade and grade not in ("0", "0.0") else ""
            items.append({"vod_id": mid, "vod_name": title,
                          "vod_pic": _fix_pic(str(it.get("cover_img") or "")),
                          "vod_remarks": remark})
        pages = int(resp.get("pages") or 0)
        if not items:                      # 空页刹车，禁止无限翻页
            pages = pg
        elif pages <= 0:
            pages = pg
        return {"page": pg, "pagecount": pages, "limit": 36,
                "total": pages * 36, "list": items}

    def categoryContent(self, tid, pg, filter, extend):
        s = self._s()
        extend = extend if isinstance(extend, dict) else {}
        params = {
            "mform": str(extend.get("mform") or tid or "all"),
            "mcountry": str(extend.get("mcountry") or "all"),
            "genre_arr": str(extend.get("genre") or "all"),
            "page": str(pg or 1),
            "sort": str(extend.get("sort") or "default"),
            "album": "all",
            "title": "",
            "years": str(extend.get("years") or "all"),
        }
        resp = s.movielist(params)
        return self._list_from_resp(resp, pg)

    def searchContent(self, key, quick, pg="1"):
        s = self._s()
        params = {
            "mform": "all",
            "mcountry": "all",
            "genre_arr": "all",
            "page": str(pg or 1),
            "sort": "default",
            "album": "all",
            "title": str(key or "").strip(),
            "years": "all",
        }
        resp = s.movielist(params)
        return self._list_from_resp(resp, pg)

    # ---- 详情 ----
    def detailContent(self, ids):
        s = self._s()
        mid = str(ids[0]) if ids else ""
        mid = mid.split("@@")[-1].strip()
        if not mid:
            return {"list": []}
        body = s.get("/play/" + mid, referer="/filter")
        html = body.decode("utf-8", "replace") if body else ""
        if not html or CHALLENGE_MARK in html[:8000]:
            return {"list": []}
        info = _detail_info(html) if html else {}
        eps = _episodes(html) if html else []
        groups = _groups(html) if html else []
        name = info.get("name") or "注视影视"
        pic = info.get("pic") or ""
        remark = " / ".join([x for x in [info.get("grade")] + info.get("tags", []) if x][:4])
        desc = info.get("desc") or ""

        base = s.host + "/play/" + mid
        if not eps:
            eps = [{"path": 0, "name": "在线观看", "id": ""}]
        # 三条线路：主线(带验证头免二次验证) / 原站 / 解析
        lines = [("注视·主线", "main"), ("注视·原站", "raw"), ("注视·解析", "parse")]
        play_from, play_url = [], []
        for lname, mode in lines:
            segs = []
            for ep in eps:
                url = "%s?p=%d" % (base, ep["path"] + 1)
                segs.append("%s$%s" % (ep["name"], url))
            play_from.append(lname)
            play_url.append("#".join(segs))
        vod = {
            "vod_id": mid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": remark,
            "vod_content": desc,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}

    # ---- 播放 ----
    def playerContent(self, flag, id, vipFlags):
        s = self._s()
        url = str(id or "")
        if not url:
            return {"parse": 0, "url": "", "header": {}}
        if not url.startswith("http"):
            url = s.host + ("/" + url.lstrip("/"))
        flag = str(flag or "")
        if "解析" in flag:                     # 交给壳子自己的解析口
            return {"parse": 1, "url": url, "header": {}}
        # 主线：过一次 Cap（cookie 3 天有效）并带上，WebView 免二次验证
        try:
            if not s.has_cap():
                s.cap_bypass()
        except Exception:
            pass
        header = {"User-Agent": UA, "Referer": s.host + "/",
                  "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        if "原站" not in flag:
            try:
                ck = _cookie_header(s)
                if ck:
                    header["Cookie"] = ck
            except Exception:
                pass
        return {"parse": 0, "url": url, "header": header}

    def localProxy(self, param=None):
        return [200, "application/json; charset=utf-8",
                json.dumps({"code": 1, "msg": "gaze"}).encode("utf-8"), {}]
