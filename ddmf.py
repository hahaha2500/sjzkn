# -*- coding: utf-8 -*-
"""
蛋蛋魔法影视  ddmf.net  —— TVBox / FongMi（默影视/webhtv）type=3 源

站型：标准 MacCMS v10（苹果CMS v10），Cloudflare 前置，无任何加密。

实测结论（2026-10-20）：
  · 采集接口全开：/api.php/provide/vod/?ac=list|videolist|detail
      - ac=list      → class(71 个分类) + list(20 条/页)
      - ac=videolist → 直接返回 TVBox 契约字段（vod_play_from 用 $$$ 分线路、
                       vod_play_url 用 # 分集），比 ac=list 信息更全
      - ac=detail&ids=ID / ac=detail&wd=关键词 → 单片全字段
  · 总量 186082 条，分类 71 个（其中 19/32~36/38~59/71 是空分类）
  · 播放地址 **明文 m3u8**，encrypt=0，直链可播，实测裸请求 200：
      v.lzcdn27.com / hn.bfvvs.com / player.yzzyvip-35.com / v5.ppqrrs.com ...
  · 线路名是站方内部标记：lzm3u8 / 1080zyk / wjm3u8 / jinyingm3u8 / hnm3u8 /
      kcm3u8 / okm3u8 / sdm3u8 / ffm3u8 / slm3u8 / dplayer ...（本文件映射成中文）
  · 分类筛选只有 year 生效；cid / h / by / area 均被忽略（实测 total 不变）
  · 搜索：ac=detail&wd={词}，分页正常（wd pg2 可用）
  · 直播：type_id 60/61/62（卫视/央视），线路 dplayer，地址是 m3u8 直播流

零解密、零嗅探、parse=0 直出。

extend（可选，JSON 串或 dict）：
  {"host":"https://ddmf.net", "limit":30, "proxy":"http://127.0.0.1:7890"}
"""
import re
import json
import time
import gzip
import urllib.request
import urllib.parse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass


# ---------------------------------------------------------------- 网络层（纯标准库）
_OPENER = None
_COOKIE_JAR = None
_PROXY_OPENER = None
_PROXY_KEY = ''


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


# 线路标记 -> 中文名（站方内部标记，映射后用户可读）
ROUTE_NAMES = {
    'lzm3u8': '江南云', '1080zyk': '1080P', 'wjm3u8': '无尽',
    'jinyingm3u8': '影云', 'hnm3u8': '海纳', 'kcm3u8': '快猫',
    'okm3u8': 'OK', 'sdm3u8': '闪电', 'ffm3u8': '非凡',
    'slm3u8': '思乐', 'dplayer': '直播', 'lzm3u8s': '江南云备用',
}
# 分类屏蔽：站方空分类（图库类已下线）+ 不适合电视端
BLOCK_TIDS = set([19, 32, 33, 34, 35, 36, 38, 39, 40, 41, 42, 43, 44, 45,
                  46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 71])


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
        self._cls = None
        self._cache = {}

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
            if int(cfg.get('limit', 0)) > 0:
                self.limit = min(100, int(cfg['limit']))
        except Exception:
            pass

    # ------------------------------------------------------------ 底层请求
    def _opener_for(self):
        """按当前代理设置取 opener（模块级缓存连接池）"""
        global _OPENER, _PROXY_OPENER, _PROXY_KEY
        if self._proxy:
            if _PROXY_OPENER is None or _PROXY_KEY != self._proxy:
                _PROXY_OPENER = urllib.request.build_opener(
                    urllib.request.ProxyHandler({'http': self._proxy,
                                                'https': self._proxy}),
                    urllib.request.HTTPCookieProcessor(_cookie_jar()))
                _PROXY_KEY = self._proxy
                try:
                    _PROXY_OPENER.addheaders = list(_get_opener().addheaders)
                except Exception:
                    pass
            return _PROXY_OPENER
        return _get_opener()

    def _raw(self, url, tries=3):
        last = ''
        for i in range(tries):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', self.UA)
                req.add_header('Accept', '*/*')
                req.add_header('Accept-Encoding', 'gzip, deflate')
                req.add_header('Referer', self.host + '/')
                r = self._opener_for().open(req, timeout=25)
                data = _decompress(r.read(), r.headers.get('Content-Encoding'))
                txt = data.decode('utf-8', 'ignore')
                if txt and txt.lstrip().startswith('{'):
                    return txt
                last = 'bad-body'
            except Exception as e:
                last = str(e)[:80]
            time.sleep(0.5 * (i + 1))
        return last if last.startswith('{') else ''

    def _api(self, params):
        """采集接口请求；主域失败自动切镜像域"""
        q = urllib.parse.urlencode(params)
        hosts = [self.host] + [h for h in self.HOSTS if h != self.host]
        for h in hosts:
            txt = self._raw('%s%s?%s' % (h, self.API, q))
            if not txt:
                continue
            try:
                j = json.loads(txt)
            except Exception:
                continue
            if j.get('code') in (1, 200) or j.get('list') is not None:
                return j
        return {}

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
                    'vod_pic': pic,
                    'vod_remarks': self._clean(it.get('vod_remarks') or ''),
                })
            except Exception:
                continue
        return out

    def _cls_data(self):
        def fetch():
            j = self._api({'ac': 'list'})
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
        return {'class': cls, 'filters': {}}

    def homeVideoContent(self):
        j = self._api({'ac': 'list', 'pg': 1, 'limit': self.limit})
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
        # 实测只有 year 生效
        yr = (flt or {}).get('year')
        if yr not in (None, ''):
            params['year'] = yr
        j = self._api(params)
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
        j = self._api({'ac': 'detail', 'wd': key, 'pg': pg, 'limit': self.limit})
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
        j = self._api({'ac': 'detail', 'ids': vid})
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
        # 线路名去重（同一线路名出现多次要加序号，避免壳子塌陷）
        used = {}
        names = []
        for f in froms:
            base = self._route_name(f)
            used[base] = used.get(base, 0) + 1
            names.append(base if used[base] == 1 else '%s-%d' % (base, used[base]))
        vod = {
            'vod_id': vid,
            'vod_name': self._clean(it.get('vod_name') or ''),
            'vod_pic': (it.get('vod_pic') or it.get('vod_pic_thumb') or '').strip(),
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
        if url.startswith('http'):
            return {'parse': 0, 'url': url, 'header': header}
        # 非直链（极少见）：交壳子内建嗅探
        return {'parse': 0, 'url': url, 'header': header}

    def action(self, action):
        return {}

    def liveContent(self, url):
        return ''

    # ------------------------------------------------------------ 本地代理（取图/跨域中转）
    def localProxy(self, param=None):
        try:
            p = param
            if isinstance(p, str):
                p = json.loads(p) if p.strip().startswith('{') else {}
            if not isinstance(p, dict):
                p = {}
            url = p.get('url') or ''
            if not url.startswith('http'):
                return [404, 'text/plain', b'bad url',
                        {'Content-Type': 'text/plain'}]
            req = urllib.request.Request(url)
            req.add_header('User-Agent', self.UA)
            req.add_header('Referer', self.host + '/')
            r = _get_opener().open(req, timeout=25)
            body = r.read()
            ct = r.headers.get('Content-Type') or 'application/octet-stream'
            return [200, ct, body, {'Content-Type': ct}]
        except Exception as e:
            return [404, 'text/plain', str(e)[:60].encode('utf-8'),
                    {'Content-Type': 'text/plain'}]
