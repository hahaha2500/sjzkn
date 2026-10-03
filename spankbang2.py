# -*- coding: utf-8 -*-
"""
SpankBang TVBox / FongMi type=3 Python Spider
站点：https://spankbang.com （自写 Alpine.js 模板，非 MacCMS，无采集接口）
结构实测（2026-10-04）：
  分类   /trending_videos/ /most_popular/ /new_videos/ /upcoming/ /top_rated/  第N页 + /{N}/
  搜索   /s/{关键词}/  第N页 /s/{关键词}/{N}/
  详情   /{vid}/video/ 或 /{vid}/video/{slug}
  列表卡 <div data-testid="video-item" data-id="..."> 内 <img src="https://tbi.sb-cd.com/t/{id}/ad/03/w:300/...">
  播放   详情页 <video id="main_video_player_html5_api" src="https://vdownload-{m}.sb-cd.com/../{id}-720p.mp4?secure=..">
          + <source src="...{id}-1080p.mp4?secure=..">  （secure 签名时效约数分钟，playerContent 现取现播不缓存）
"""

import re
import gzip
import json
import ssl
import zlib
import urllib.request
import urllib.parse
import urllib.error

try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        pass


class Spider(_BaseSpider):
    HOSTS = [
        'https://spankbang.com',
        'https://spankbang.party',
    ]
    UAS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    ]
    CLS = [
        ('trending_videos', 'Trending'),
        ('most_popular', 'Most Popular'),
        ('new_videos', 'New Videos'),
        ('upcoming', 'Upcoming'),
        ('top_rated', 'Top Rated'),
        ('long_videos', 'Long Videos'),
    ]
    BAD_IMG = ('placeholder', 'loading', 'blank', '1x1', 'logo', 'avatar', '.svg')
    # 类级默认值：宿主若未调用 init() 也不会 AttributeError
    hosts = list(HOSTS)
    ua = UAS[0]
    proxy = None
    timeout = 15
    _page_cache = {}
    _ua_i = 0

    # ---------- 基础设施 ----------
    def getName(self):
        return 'SpankBang'

    def getDependence(self):
        return []

    def init(self, extend=''):
        self.hosts = list(self.HOSTS)
        self.ua = self.UAS[0]
        self.proxy = None
        self._page_cache = {}
        self._ua_i = 0
        self.timeout = 15
        try:
            e = extend
            if isinstance(e, str):
                e = json.loads(e) if e.strip().startswith('{') else {'host': e}
            if isinstance(e, dict):
                h = e.get('host') or e.get('site_url') or e.get('url')
                if h and str(h).startswith('http'):
                    self.hosts = [str(h).rstrip('/')] + self.hosts
                p = e.get('proxy')
                if p:
                    self.proxy = str(p).rstrip('/')
        except Exception:
            pass

    def destroy(self):
        pass

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(mp4|m3u8|flv|mkv)(\?|$)', str(url), re.I))

    def manualVideoCheck(self):
        return False

    def _cookie(self):
        c = 'country=US; mobile=off; age_verified=1; remember_age_check=1'
        try:
            from android.webkit import CookieManager
            s = CookieManager.getInstance().getCookie(self._host0())
            if s and 'cf_clearance' in s:
                c = c + '; ' + s
        except Exception:
            pass
        return c

    def _headers(self, host, url=''):
        self._ua_i = (self._ua_i + 1) % len(self.UAS)
        return {
            'User-Agent': self.UAS[self._ua_i],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': url.rsplit('/', 1)[0] + '/' if url else host + '/',
            'Cookie': self._cookie(),
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Upgrade-Insecure-Requests': '1',
            'Connection': 'keep-alive',
        }

    def _raw(self, url, timeout=None):
        timeout = timeout or getattr(self, 'timeout', 15)
        """返回 bytes；urllib 失败回落壳子内建 OkHttp（继承系统代理与 Cookie）"""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        host = re.sub(r'^https?://([^/]+).*$', r'\1', url)
        try:
            req = urllib.request.Request(url, headers=self._headers('https://' + host, url))
            op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
            if self.proxy:
                op = urllib.request.build_opener(
                    urllib.request.ProxyHandler({'http': self.proxy, 'https': self.proxy}),
                    urllib.request.HTTPSHandler(context=ctx))
            with op.open(req, timeout=timeout) as r:
                data = r.read()
                enc = (r.info().get('Content-Encoding') or '').lower()
            if data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)
            elif data[:1] == b'\x78':
                try:
                    data = zlib.decompress(data)
                except Exception:
                    pass
            return data
        except Exception:
            pass
        try:
            from com.github.catvod.net import OkHttp
            from java.util import HashMap
            jh = HashMap()
            for k, v in self._headers('https://' + host, url).items():
                jh.put(k, v)
            resp = OkHttp.string(url, jh)
            if resp:
                return str(resp).encode('utf-8', 'ignore')
        except Exception:
            pass
        return b''

    def _get(self, url, ttl=0):
        if ttl and url in self._page_cache:
            ts, txt = self._page_cache[url]
            if _now() - ts < ttl:
                return txt
        last = ''
        for host in self.hosts:
            u = url if url.startswith('http') else host + url
            data = self._raw(u, timeout=getattr(self, 'timeout', 15))
            if not data:
                continue
            txt = data.decode('utf-8', 'ignore')
            if len(txt) < 500:
                continue
            if 'Just a moment' in txt or 'cf-browser-verification' in txt or 'Enable JavaScript and cookies' in txt:
                last = 'CF'
                continue
            if ttl:
                self._page_cache[url] = (_now(), txt)
            return txt
        return last if last == 'CF' else ''

    # ---------- 解析 ----------
    RE_CARD = re.compile(r'<div data-testid="video-item".*?(?=<div data-testid="video-item"|<footer|</main)', re.S)
    RE_VID = re.compile(r'href="(?:https?://[^/]+)?/([a-zA-Z0-9]{2,12})/video/([^"]*)"', re.I)
    RE_IMG = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
    RE_LEN = re.compile(r'data-testid="video-item-length"[^>]*>\s*([^<]{1,12})<', re.I)
    RE_NAME = re.compile(r'<a[^>]+title="([^"]{2,300})"', re.I)

    def _cards(self, html):
        out = []
        seen = set()
        if not html:
            return out
        for blk in self.RE_CARD.findall(html):
            m = self.RE_VID.search(blk)
            if not m:
                continue
            vid, slug = m.group(1), m.group(2)
            if vid in seen:
                continue
            im = self.RE_IMG.search(blk)
            pic = im.group(1) if im else ''
            if any(b in pic.lower() for b in self.BAD_IMG) or self.hosts == []:
                pic = ''
            if pic.startswith('//'):
                pic = 'https:' + pic
            elif pic and pic.startswith('/'):
                pic = self._host0() + pic
            nm = self.RE_NAME.search(blk)
            name = nm.group(1).strip() if nm else urllib.parse.unquote_plus(slug).replace('-', ' ').strip()
            name = re.sub(r'\s+', ' ', name.replace('&#039;', "'").replace('&amp;', '&'))
            if not name or len(name) < 2:
                name = 'Video ' + vid
            lm = self.RE_LEN.search(blk)
            note = re.sub(r'\s+', '', lm.group(1)) if lm else 'HD'
            seen.add(vid)
            out.append({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': pic,
                'vod_remarks': note,
            })
        return out

    def _pagecount(self, html, pg):
        pc = 0
        for m in re.finditer(r'href="(?:https?://[^/]+)?/[^"]*?/(\d{1,4})/"', html or ''):
            try:
                n = int(m.group(1))
                if n > pc:
                    pc = n
            except Exception:
                pass
        if pc:
            return max(pc, pg)
        return pg + 1

    # ---------- 六接口 ----------
    def homeContent(self, filter=False):
        return {
            'class': [{'type_id': a, 'type_name': b} for a, b in self.CLS],
            'filters': {},
        }

    def homeVideoContent(self):
        v = self._cards(self._get('/trending_videos/', ttl=300))
        return {'list': v[:24]}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        path = '/%s/' % tid if pg == 1 else '/%s/%d/' % (tid, pg)
        html = self._get(path, ttl=300)
        v = self._cards(html)
        if not v:
            return {'page': pg, 'pagecount': pg, 'limit': 0, 'total': 0, 'list': []}
        return {'page': pg, 'pagecount': self._pagecount(html, pg), 'limit': len(v),
                'total': len(v) * self._pagecount(html, pg), 'list': v}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid).split('@@')[0]
        html = self._get('/%s/video/' % vid)
        if not html:
            html = self._get('/%s/video/' % vid, ttl=300)
        title = ''
        m = re.search(r'<h1[^>]*data-testid="video-title"[^>]*>(.*?)</h1>', html, re.S)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1))
        if not title:
            m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
            if m:
                title = re.sub(r'<[^>]+>', '', m.group(1))
        if not title:
            m = re.search(r'property="og:title" content="([^"]+)"', html)
            title = m.group(1).split(': Porn')[0] if m else vid
        title = re.sub(r'\s+', ' ', title.replace('&amp;', '&').replace('&#039;', "'")).strip()

        pic = ''
        m = re.search(r'id="player_cover_img"[^>]+src="([^"]+)"', html) or \
            re.search(r'property="og:image" content="([^"]+)"', html)
        if m:
            pic = m.group(1)

        desc = ''
        m = re.search(r'data-testid="video-description"[^>]*>(.*?)</div>', html, re.S)
        if m:
            desc = re.sub(r'<[^>]+>', ' ', m.group(1))
            desc = re.sub(r'\s+', ' ', desc).strip()[:600]

        dur = ''
        m = re.search(r'property="og:video:duration" content="(\d+)"', html)
        if m:
            dur = '%d:%02d' % (int(m.group(1)) // 60, int(m.group(1)) % 60)
        if not dur:
            lm = self.RE_LEN.search(html)
            dur = re.sub(r'\s+', '', lm.group(1)) if lm else 'HD'

        vod = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': pic,
            'vod_remarks': dur,
            'vod_year': '',
            'vod_area': '',
            'vod_actor': '',
            'vod_director': '',
            'vod_content': desc,
            'vod_play_from': 'SpankBang·直连$$$SpankBang·原页嗅探',
            'vod_play_url': '%s$$$%s' % ('正片$' + vid, '正片$sniff@@' + vid),
        }
        return {'list': [vod]}

    def searchContent(self, key, quick=False, pg='1'):
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        kw = urllib.parse.quote_plus(str(key).strip())
        html = self._get('/s/%s/' % kw if pg == 1 else '/s/%s/%d/' % (kw, pg), ttl=120)
        v = self._cards(html)
        pc = self._pagecount(html, pg)
        return {'page': pg, 'pagecount': pc, 'limit': len(v), 'total': len(v) * pc, 'list': v}

    def _host0(self):
        return self.hosts[0] if self.hosts else self.HOSTS[0]

    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id)
        sniff = pid.startswith('sniff@@')
        vid = pid[7:] if sniff else pid.split('@@')[0]
        url = '%s/%s/video/' % (self._host0(), vid)
        head = self._headers(self._host0(), url)
        if not str(id or '').strip():
            return {'parse': 0, 'url': '', 'header': {}}
        if sniff or '嗅探' in str(flag):
            return {'parse': 0, 'url': url, 'header': head}
        html = self._get('/%s/video/' % vid)  # 时效签名，绝不缓存
        best = ''
        cands = []
        m = re.search(r'<video[^>]+id="main_video_player_html5_api"[^>]+src="([^"]+)"', html, re.S)
        if m:
            cands.append(m.group(1))
        for s in re.findall(r'<source[^>]+src="([^"]+)"', html):
            cands.append(s)
        for u in cands:
            u = u.replace('&amp;', '&')
            if '.mp4' not in u.lower() and '.m3u8' not in u.lower():
                continue
            if 'vdownload' not in u:
                continue  # tbv.sb-cd.com 的 td.mp4 是列表页预览短片，不是正片
            if '1080p' in u:
                best = u
                break
            if not best:
                best = u
        if best:
            return {'parse': 0, 'url': best, 'header': head}
        return {'parse': 0, 'url': url, 'header': head}

    def localProxy(self, param=None):
        try:
            p = param
            if isinstance(p, str):
                p = json.loads(p)
            if not isinstance(p, dict):
                return [404, 'text/plain', b'', {}]
            u = p.get('url') or ''
            if u.startswith('http') and ('tbi.sb-cd.com' in u or 'tbv.sb-cd.com' in u):
                data = self._raw(u, timeout=12)
                if data and data[:1] in (b'\xff', b'\x89', b'G'):
                    return [200, 'image/jpeg', data, {'Cache-Control': 'max-age=86400'}]
            return [404, 'text/plain', b'', {}]
        except Exception:
            return [404, 'text/plain', b'', {}]


def _now():
    import time
    return time.time()