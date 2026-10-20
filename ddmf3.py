# -*- coding: utf-8 -*-
"""
蛋蛋魔法影视  ddmf.net  —— TVBox / FongMi（默影视/webhtv）type=3 源

站型：标准 MacCMS v10（苹果CMS），Cloudflare 前置，无任何加密。
采集接口全开：/api.php/provide/vod/?ac=list|videolist|detail
  · 总量 186082 条，分类 71 个（其中 29 个是站方空分类，已剔除）
  · 播放地址明文 m3u8（encrypt=0），零解密零嗅探，parse=0 直出
  · 线路标记 lzm3u8/1080zyk/wjm3u8/jinyingm3u8/hnm3u8/kcm3u8/okm3u8/
    sdm3u8/ffm3u8/slm3u8/dplayer —— 已映射成中文名

★ v2 改动（2026-10-20，针对「加载不出任何分类和内容」）：
  1. 取站双通道：直连 ddmf.net 优先，失败自动切公共前置代理通道
     （jsnzkpg4 / down.nigx.cn / seep.eu.org，均实测可代理 ddmf.net 任意页面）
  2. 全部失败时不再白屏 → 返回「[诊断]」卡片，写明每个通道的失败原因
  3. 封面改走本地代理 ?do=py&type=img&url=<b64>（源控 Referer，规避图床防盗链）
  4. 分通道记健康状态，记住上次可用的通道（模块级，壳每次 new Spider 也有效）

extend（可选，JSON 串或 dict）：
  {"host":"https://ddmf.net", "limit":30, "proxy":"http://127.0.0.1:7890",
   "channels":1}      # channels=0 关闭代理通道只用直连
"""
import re
import json
import time
import base64
import gzip
import socket
import urllib.request
import urllib.parse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass


# 站方内部线路标记 -> 中文名
ROUTE_NAMES = {
    'lzm3u8': '江南云', '1080zyk': '1080P', 'wjm3u8': '无尽',
    'jinyingm3u8': '影云', 'hnm3u8': '海纳', 'kcm3u8': '快猫',
    'okm3u8': 'OK', 'sdm3u8': '闪电', 'ffm3u8': '非凡',
    'slm3u8': '思乐', 'dplayer': '直播',
}
# 站方空分类（图库类已全部下线）+ 无内容分类
BLOCK_TIDS = set([19, 32, 33, 34, 35, 36, 38, 39, 40, 41, 42, 43, 44, 45,
                  46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 71])

# 取站通道：直连 + 公共前置代理（都是路径包裹式，实测能代理 ddmf.net 全部路径）
PROXY_CHANNELS = [
    'https://jsnzkpg4.pages.dev/',
    'https://down.nigx.cn/?url=',
    'https://seep.eu.org/',
]

_OPENER = None
_COOKIE_JAR = None
_PROXY_OPENER = None
_PROXY_KEY = ''
# 模块级通道健康表（壳每次 new Spider，实例级会归零）
_CH_STATE = {'good': '', 'err': {}}

# 1x1 透明 PNG（程序生成，CRC 正确，68 字节）—— 取图全失败时兜底，不留裂图
_PNG1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000"
    "000b4944415478da636000020000050001e9fadcd80000000049454e44ae426082")


# ---------------------------------------------------------------- 网络层
def _cookie_jar():
    global _COOKIE_JAR
    if _COOKIE_JAR is None:
        try:
            import http.cookiejar
            _COOKIE_JAR = http.cookiejar.CookieJar()
        except Exception:
            _COOKIE_JAR = None
    return _COOKIE_JAR


def _get_opener():
    global _OPENER
    if _OPENER is None:
        jar = _cookie_jar()
        try:
            if jar is not None:
                _OPENER = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(jar))
            else:
                _OPENER = urllib.request.build_opener()
        except Exception:
            _OPENER = urllib.request.build_opener()
        try:
            _OPENER.addheaders = [
                ('User-Agent', Spider.UA),
                ('Accept', '*/*'),
                ('Accept-Language', 'zh-CN,zh;q=0.9'),
            ]
        except Exception:
            pass
    return _OPENER


def _decompress(raw, enc):
    if not raw:
        return raw
    if enc and 'gzip' in enc.lower():
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    if raw[:2] == b'\x1f\x8b':
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    return raw


def _chan_url(chan, target):
    """按通道类型拼最终 URL：
       path  → 前缀 + 目标URL（路径包裹式）
       query → 前缀 + urlencode(目标URL)（参数式，缺 ?url= 会静默返回落地页）"""
    kind, val = chan
    if kind == 'query':
        return val + urllib.parse.quote(target, safe='')
    return val + target


# ---------------------------------------------------------------- Spider
class Spider(BaseSpider):
    HOSTS = ['https://ddmf.net', 'https://www.ddmf.net']
    NAME = '蛋蛋魔法影视'
    UA = ('Mozilla/5.0 (Linux; Android 13; M2102J2SC) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')
    LIMIT = 30
    TTL = 300
    API = '/api.php/provide/vod/'

    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = self.HOSTS[0]
        self.limit = self.LIMIT
        self._proxy = ''
        self._use_chan = True
        self._cls = None
        self._cache = {}
        self._proxy_base = ''

    def getName(self):
        return self.NAME

    def isVideoFormat(self, url):
        return any(x in (url or '').lower()
                   for x in ['.m3u8', '.mp4', '.flv', '.mkv', '.ts', '.mpd'])

    def manualVideoCheck(self):
        return False

    def getDependence(self):
        return []

    # ------------------------------------------------------------ 初始化
    def init(self, extend=""):
        cfg = {}
        try:
            if isinstance(extend, dict):
                cfg = extend
            elif isinstance(extend, str) and extend.strip():
                s = extend.strip()
                cfg = json.loads(s) if s.startswith('{') else (
                    {'host': s} if s.startswith('http') else {})
        except Exception:
            cfg = {}
        h = cfg.get('host')
        if isinstance(h, str) and h.startswith('http'):
            self.host = h.rstrip('/')
        p = cfg.get('proxy')
        if isinstance(p, str) and p.strip():
            self._proxy = p.strip()
        try:
            self._use_chan = int(cfg.get('channels', 1)) != 0
        except Exception:
            self._use_chan = True
        try:
            if int(cfg.get('limit', 0)) > 0:
                self.limit = min(100, int(cfg['limit']))
        except Exception:
            pass
        self._proxy_base = self._auto_proxy_base()

    def _auto_proxy_base(self):
        """取壳的本地代理服务地址（默影视/webhtv 用 Proxy.getUrl，失败则扫端口）"""
        try:
            from com.github.catvod import Proxy
            u = Proxy.getUrl(True)
            if u:
                return u
        except Exception:
            pass
        for port in range(9978, 9999):
            try:
                sk = socket.socket()
                sk.settimeout(0.05)
                r = sk.connect_ex(('127.0.0.1', port))
                sk.close()
                if r == 0:
                    return 'http://127.0.0.1:%d' % port
            except Exception:
                pass
        return ''

    # ------------------------------------------------------------ 底层请求
    def _opener_for(self):
        global _PROXY_OPENER, _PROXY_KEY
        if self._proxy:
            if _PROXY_OPENER is None or _PROXY_KEY != self._proxy:
                try:
                    jar = _cookie_jar()
                    hs = [urllib.request.ProxyHandler({'http': self._proxy,
                                                      'https': self._proxy})]
                    if jar is not None:
                        hs.append(urllib.request.HTTPCookieProcessor(jar))
                    _PROXY_OPENER = urllib.request.build_opener(*hs)
                    _PROXY_KEY = self._proxy
                    try:
                        _PROXY_OPENER.addheaders = list(_get_opener().addheaders)
                    except Exception:
                        pass
                except Exception:
                    _PROXY_OPENER = None
            return _PROXY_OPENER or _get_opener()
        return _get_opener()

    def _fetch(self, url, tries=2, timeout=18):
        """单 URL 请求，返回 (文本, 错误原因)"""
        last = ''
        for i in range(tries):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', self.UA)
                req.add_header('Accept', '*/*')
                req.add_header('Accept-Encoding', 'gzip, deflate')
                req.add_header('Referer', self.host + '/')
                r = self._opener_for().open(req, timeout=timeout)
                data = _decompress(r.read(), r.headers.get('Content-Encoding'))
                return data.decode('utf-8', 'ignore'), ''
            except Exception as e:
                last = '%s: %s' % (type(e).__name__, str(e)[:60])
            time.sleep(0.4 * (i + 1))
        return '', last

    # ------------------------------------------------------------ 通道调度
    def _channels(self):
        """返回 [(kind, prefix), ...]，直连优先；上次可用的通道排最前"""
        chans = [('direct', self.host), ('direct', self.HOSTS[1])]
        if self._use_chan:
            for p in PROXY_CHANNELS:
                chans.append(('path', p))
            chans.append(('query', 'https://down.nigx.cn/?url='))
        good = _CH_STATE.get('good') or ''
        if good:
            chans.sort(key=lambda c: 0 if good in (c[1],) else 1)
        return chans

    def _api(self, params, need_json=True):
        """采集接口请求：按通道逐个试；返回 (json, 诊断信息)"""
        q = urllib.parse.urlencode(params)
        path = self.API + '?' + q
        errs = []
        for kind, prefix in self._channels():
            if kind == 'direct':
                url = prefix + path
            elif kind == 'query':
                url = _chan_url(('query', prefix), self.host + path)
            else:
                url = _chan_url(('path', prefix), self.host + path)
            txt, err = self._fetch(url, tries=1, timeout=20)
            if not txt:
                errs.append('%s -> %s' % (prefix[:34], err or 'empty'))
                continue
            if not txt.lstrip().startswith('{'):
                errs.append('%s -> not-json(%s)' % (prefix[:34], txt[:30]))
                continue
            try:
                j = json.loads(txt)
            except Exception as e:
                errs.append('%s -> bad-json(%s)' % (prefix[:34], str(e)[:30]))
                continue
            if j.get('code') in (1, 200) or j.get('list') is not None:
                _CH_STATE['good'] = prefix
                _CH_STATE['err'] = {}
                return j, ''
            errs.append('%s -> code=%s' % (prefix[:34], j.get('code')))
        return {}, errs[:6]

    def _diag_card(self, errs):
        """取站全失败时的诊断卡（写进首页，用户截图即可定位）"""
        lines = ['[诊断] 蛋蛋魔法影视 取站失败', '',
                 '直连域名: %s' % self.host, '',
                 '各通道失败原因:']
        if isinstance(errs, str):
            errs = [errs]
        for e in (errs or ['(无记录)']):
            lines.append('  - ' + str(e)[:130])
        lines += ['', '已试通道: 直连 x2 + 公共代理 x3',
                  '如手机浏览器也打不开 ddmf.net，说明你的网络被 Cloudflare 拦了。',
                  '可试: 开代理 / 换网络 / 用 extend 指定可用域名']
        return {'list': [{'vod_id': 'diag', 'vod_name': '\n'.join(lines),
                          'vod_pic': '', 'vod_remarks': '诊断'}]}

    def _cached(self, key, fn, ttl=None):
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < (ttl or self.TTL):
            return hit[1]
        val = fn()
        if val:
            if len(self._cache) > 300:
                self._cache.clear()
            self._cache[key] = (now, val)
        return val

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _clean(s):
        s = str(s or '')
        for ch in ['$', '#']:
            s = s.replace(ch, '')
        return s.strip()

    @staticmethod
    def _route_name(raw):
        raw = str(raw or '').strip()
        if not raw:
            return '线路'
        return ROUTE_NAMES.get(raw.lower(), raw)

    def _pic(self, url):
        """封面优先走本地代理（源控 Referer + 通道兜底，规避图床防盗链/被墙）；
        拿不到壳的代理地址时回退原图，绝不返回空"""
        u = (url or '').strip()
        if not u:
            return ''
        if not self._proxy_base:
            return u
        b = base64.urlsafe_b64encode(u.encode('utf-8')).decode('ascii').rstrip('=')
        return '%s?do=py&type=img&url=%s' % (self._proxy_base, b)

    def _items(self, lst):
        out = []
        for it in (lst or []):
            try:
                vid = str(it.get('vod_id') or '').strip()
                if not vid:
                    continue
                nm = self._clean(it.get('vod_name') or '')
                if not nm:
                    continue
                pic = (it.get('vod_pic') or it.get('vod_pic_thumb') or '').strip()
                out.append({
                    'vod_id': vid,
                    'vod_name': nm,
                    'vod_pic': self._pic(pic),
                    'vod_remarks': self._clean(it.get('vod_remarks') or ''),
                })
            except Exception:
                continue
        return out

    def _cls_data(self):
        def fetch():
            j, e = self._api({'ac': 'list'})
            return j if j.get('class') else {}
        j = self._cached('cls', fetch, 1800)
        if j.get('class'):
            self._cls = []
            for c in j['class']:
                try:
                    tid = int(c.get('type_id'))
                except Exception:
                    continue
                if tid in BLOCK_TIDS:
                    continue
                self._cls.append({'type_id': str(tid),
                                  'type_name': str(c.get('type_name') or '')})
        return j

    # ------------------------------------------------------------ 六接口
    def homeContent(self, filter=False):
        self._cls_data()
        cls = self._cls or []
        if not cls:
            j, e = self._api({'ac': 'list'})
            card = self._diag_card(e)
            return {'class': [{'type_id': 'diag', 'type_name': '诊断:取站失败'}],
                    'filters': {}, 'list': card.get('list')}
        return {'class': cls, 'filters': {}}

    def homeVideoContent(self):
        j, e = self._api({'ac': 'list', 'pg': 1, 'limit': self.limit})
        if not j:
            return self._diag_card(e)
        return {'list': self._items(j.get('list'))}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        params = {'ac': 'list', 't': tid, 'pg': pg, 'limit': self.limit}
        flt = filter if isinstance(filter, dict) else {}
        if not flt and isinstance(extend, dict):
            flt = extend
        yr = (flt or {}).get('year')
        if yr not in (None, ''):
            params['year'] = yr
        j, e = self._api(params)
        if not j:
            return self._diag_card(e)
        items = self._items(j.get('list'))
        try:
            pc = int(j.get('pagecount') or 0) or pg
        except Exception:
            pc = pg
        try:
            total = int(j.get('total') or 0)
        except Exception:
            total = 0
        if not items and pg > 1:
            pc = pg
        return {'list': items, 'page': pg, 'pagecount': max(pc, pg),
                'limit': self.limit, 'total': total}

    def searchContent(self, key, quick=False, pg='1'):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        j, e = self._api({'ac': 'detail', 'wd': key, 'pg': pg, 'limit': self.limit})
        if not j:
            return self._diag_card(e)
        items = self._items(j.get('list'))
        try:
            pc = int(j.get('pagecount') or 0) or pg
        except Exception:
            pc = pg
        try:
            total = int(j.get('total') or 0)
        except Exception:
            total = len(items)
        if not items and pg > 1:
            pc = pg
        return {'list': items, 'page': pg, 'pagecount': max(pc, pg),
                'limit': self.limit, 'total': total}

    def detailContent(self, ids):
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ''
        else:
            vid = str(ids or '')
        vid = vid.split('@')[-1].split(',')[0].strip()
        if not vid:
            return {'list': []}
        j, e = self._api({'ac': 'detail', 'ids': vid})
        if not j:
            return self._diag_card(e)
        lst = j.get('list') or []
        if not lst:
            return {'list': []}
        it = lst[0]
        froms = [x for x in str(it.get('vod_play_from') or '').split('$$$') if x.strip()]
        urls = [x for x in str(it.get('vod_play_url') or '').split('$$$') if x.strip()]
        if not urls:
            return {'list': []}
        if len(froms) < len(urls):
            froms = froms + ['线路%d' % (i + 1) for i in range(len(froms), len(urls))]
        elif len(froms) > len(urls):
            froms = froms[:len(urls)]
        used = {}
        names = []
        for f in froms:
            base = self._route_name(f)
            used[base] = used.get(base, 0) + 1
            names.append(base if used[base] == 1 else '%s-%d' % (base, used[base]))
        vod = {
            'vod_id': vid,
            'vod_name': self._clean(it.get('vod_name') or ''),
            'vod_pic': self._pic((it.get('vod_pic') or it.get('vod_pic_thumb') or '').strip()),
            'vod_remarks': self._clean(it.get('vod_remarks') or ''),
            'type_name': it.get('type_name') or '',
            'vod_year': str(it.get('vod_year') or ''),
            'vod_area': str(it.get('vod_area') or ''),
            'vod_actor': self._clean(it.get('vod_actor') or ''),
            'vod_director': self._clean(it.get('vod_director') or ''),
            'vod_content': self._clean(it.get('vod_content') or it.get('vod_blurb') or ''),
            'vod_play_from': '$$$'.join(names),
            'vod_play_url': '$$$'.join(urls),
        }
        return {'list': [vod]}

    def playerContent(self, flag, id, vipFlags=None):
        url = str(id or '')
        if '$$$' in url:
            url = url.split('$$$')[0]
        if '#' in url:
            url = url.split('#')[0]
        if '$' in url:
            url = url.split('$')[-1]
        url = url.strip()
        header = {'User-Agent': self.UA}
        return {'parse': 0, 'url': url, 'header': header}

    def action(self, action):
        return {}

    def liveContent(self, url):
        return ''

    # ------------------------------------------------------------ 本地代理（封面中转）
    def localProxy(self, param=None):
        """取图通道：直连 → 公共代理 → 1x1 合法 PNG 兜底（不留裂图）"""
        try:
            p = param
            if isinstance(p, str):
                p = json.loads(p) if p.strip().startswith('{') else {}
            if not isinstance(p, dict):
                p = {}
            raw = p.get('url') or ''
            b = raw.replace('-', '+').replace('_', '/')
            real = base64.b64decode(b + '=' * (-len(b) % 4)).decode('utf-8', 'replace')
            if not real.startswith('http'):
                return [404, 'text/plain', b'bad url', {'Content-Type': 'text/plain'}]
            for kind, prefix in self._channels():
                if kind == 'direct':
                    url = prefix + real[len(self.host):] if real.startswith(self.host) else real
                elif kind == 'query':
                    url = _chan_url(('query', prefix), real)
                else:
                    url = _chan_url(('path', prefix), real)
                try:
                    req = urllib.request.Request(url)
                    req.add_header('User-Agent', self.UA)
                    req.add_header('Accept', 'image/avif,image/webp,image/*,*/*;q=0.8')
                    req.add_header('Referer', self.host + '/')
                    r = self._opener_for().open(req, timeout=20)
                    body = r.read()
                    if body and body[:1] != b'<':
                        ct = r.headers.get('Content-Type') or 'image/jpeg'
                        return [200, ct, body,
                                {'Content-Type': ct, 'Cache-Control': 'max-age=86400'}]
                except Exception:
                    continue
            return [200, 'image/png', _PNG1, {'Content-Type': 'image/png'}]
        except Exception as e:
            return [404, 'text/plain', str(e)[:60].encode('utf-8'),
                    {'Content-Type': 'text/plain'}]
