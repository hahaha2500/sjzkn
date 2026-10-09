# -*- coding: utf-8 -*-
"""
磁力熊 cilixiong.org —— TVBox/FongMi type=3 源
站型：EmpireCMS(帝国CMS) 自写 Bootstrap 模板，纯磁力下载站，无在线流

【逆向要点 · 换站必读】
1. 无采集接口（/api.php/provide/vod 404），HTML 直抓。
2. 分类：/movie/ 电影、/drama/ 剧集；分页 /movie/index_{N}.html（第1页无页码）。
3. 详情：/movie/{id}.html 与 /drama/{id}.html（数字 id）。
4. 榜单/专题：/subject.html -> /top250/ /s/imdbtop250/ /s/{类型}/ 等
5. 搜索：POST /e/search/index.php 表单
      classid=1,2 & show=title & tempid=1 & keyboard={词}
   → 302 到 /e/search/result/?searchid={N}，★该结果页可直接 GET 复用（不必每次搜）。
6. 卡片：<div class="col"><div class="card card-cover ...">
      <a href="/movie/4758.html">
        <div class="card-img" style="background-image: url('https://i.nacloud.cc/2025/05292.jpg')">
        <h2 class="... h4">片名</h2>
        <span class="rank ...">9.7</span>  <li ...>2025</li>
   ★海报在 CSS background-image 里，不在 <img>，必须单独正则取。
7. 详情磁力：a[data-clipboard-text] 或裸 magnet:?xt=urn:btih: 正则，注意 &amp; 还原。
"""
import re
import json
import time
from urllib.parse import quote, urljoin

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self):
            pass


class Spider(BaseSpider):
    HOSTS = ['https://www.cilixiong.org', 'https://cilixiong.uk', 'https://cilixiong.xyz']
    NAME = '磁力熊'
    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

    # (type_id, 名称, 列表URL前缀, 分页模板)
    CLASSES = [
        ('movie', '电影', '/movie/', '/movie/index_%d.html'),
        ('drama', '剧集', '/drama/', '/drama/index_%d.html'),
        ('top250', '豆瓣Top250', '/top250/', ''),
        ('imdbtop250', 'IMDbTop250', '/s/imdbtop250/', ''),
    ]

    RE_ITEM = re.compile(r'<a href="/(movie|drama)/(\d+)\.html">(.*?)</a>', re.S)
    RE_PAGE = re.compile(r'/index_(\d+)\.html')
    RE_BG = re.compile(r"background-image:\s*url\('([^']+)'\)")

    def __init__(self):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.host = self.HOSTS[0]
        self.extend = {}
        self.ext = {}
        self._sess = None

    # ---------------- 壳契约 ----------------
    def init(self, extend=""):
        self.extend = extend if extend is not None else ""
        self.ext = {}
        try:
            e = extend
            if isinstance(e, dict):
                self.ext = e
            elif isinstance(e, list):
                self.ext = e[0] if e else {}
            elif isinstance(e, str) and e.strip():
                t = e.strip()
                if t.startswith('{'):
                    self.ext = json.loads(t)
        except Exception:
            self.ext = {}
        hosts = self.ext.get('hosts')
        if isinstance(hosts, list) and hosts:
            self.host = str(hosts[0]).rstrip('/')
        else:
            h = str(self.ext.get('host', '')).strip()
            if h:
                self.host = h if h.startswith('http') else 'https://' + h

    def getName(self):
        return self.NAME

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        try:
            return any(x in (url or '').lower() for x in
                       ('.m3u8', '.mp4', '.flv', '.ts', '.mkv', '.avi', '.mpg'))
        except Exception:
            return False

    def manualVideoCheck(self):
        return False

    def localProxy(self, param=None):
        return [404, 'text/plain', b'', {}]

    def action(self, aid):
        return False

    def liveContent(self, url):
        return ''

    # ---------------- 业务接口 ----------------
    def homeContent(self, filter):
        html = self._get(self.host + '/')
        cls = [{'type_id': t, 'type_name': n} for t, n, _, _ in self.CLASSES]
        vods = self._cards(html)
        out = {'class': cls, 'filters': {'class': cls}, 'list': vods,
               'page': 1, 'pagecount': 1, 'limit': len(vods), 'total': len(vods)}
        if not vods:
            out['list'] = [self._diag('抓到首页但解析0条, HTML长度=%d' % len(html or ''))]
        return out

    def homeVideoContent(self):
        html = self._get(self.host + '/')
        vods = self._cards(html)
        return {'list': vods, 'page': 1, 'pagecount': 1,
                'limit': len(vods), 'total': len(vods)}

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or '').strip()
        try:
            pg = int(pg)
            if pg < 1:
                pg = 1
        except Exception:
            pg = 1
        url = self._list_url(tid, pg)
        html = self._get(url)
        vods = self._cards(html)
        pc = self._pagecount(html, pg)
        if not vods:
            pc = max(pg, 1)
        return {'list': vods, 'page': pg, 'pagecount': pc,
                'limit': len(vods), 'total': len(vods) * pc}

    def detailContent(self, ids):
        vid = self._pid(ids)
        # vod_id 形如 "movie@4758" / "drama@4759"，拆出类型与数字id
        kind, num = 'movie', vid
        if '@' in vid:
            kind, num = vid.split('@', 1)
        if not num.isdigit():
            m = re.search(r'(\d+)', num)
            num = m.group(1) if m else ''
        url = '%s/%s/%s.html' % (self.host, kind, num) if num else self.host
        html = self._get(url)
        if not html or '剧情简介' not in html:
            for pref in ('movie', 'drama'):
                if pref == kind:
                    continue
                html2 = self._get('%s/%s/%s.html' % (self.host, pref, num))
                if html2 and '剧情简介' in html2:
                    html = html2
                    kind = pref
                    break
        if not html or '剧情简介' not in html:
            return {'list': [{'vod_id': vid, 'vod_name': vid,
                              'vod_pic': '', 'vod_remarks': '未取到', 'vod_year': '',
                              'vod_area': '', 'vod_class': '', 'vod_content': '',
                              'vod_play_from': '磁力', 'vod_play_url': '磁力$' + url}]}
        name = self._detail_name(html) or vid
        # 详情页真实结构：
        #   <div class="mv_detail"><h1>片名</h1><p>豆瓣评分: <span class="db_rank">9.7</span></p>
        #   <p>类型：|纪录片|</p><p>片长：109 分钟</p><p>上映地区：日本</p><p>主演：X</p>
        #   <div class="mv_card_box">简介正文</div>
        score = ''
        ms = re.search(r'class="db_rank">([\d.]+)<', html)
        if ms:
            score = ms.group(1)
        txt = self._text(html)
        year = self._f(r'上映日期[:：]\s*([\d]{4})', txt) or self._f(r'([\d]{4})', txt)
        area = self._f(r'上映地区[:：]\s*([^|]{1,30})', txt)
        genre = self._f(r'类型[:：]\s*([^|]{1,60})', txt)
        genre = self._clean(genre).strip('|').replace('|', ' / ').strip(' /')
        if not genre:
            mg = re.search(r'类型[:：]?\s*(.*?)</p>', html, flags=re.S)
            if mg:
                genre = self._clean(re.sub(r'<[^>]+>', ' ', mg.group(1))).strip('|').replace('|', ' / ').strip(' /')
        length = self._f(r'片长[:：]\s*([^|]{1,20})', txt)
        actor = self._f(r'主演[:：]\s*([^|]{1,120})', txt)
        alias = self._f(r'又名[:：]\s*([^|]{1,120})', txt)
        intro = ''
        mi = re.search(r'class="mv_card_box"[^>]*>(.*?)</div>', html, flags=re.S)
        if mi:
            intro = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', mi.group(1))).strip()
        if not intro:
            intro = self._f(r'剧情简介[:：]?\s*([^|]{2,800})', txt)
        magnet = ''
        m = re.search(r'data-clipboard-text="(magnet:\?[^"]+)"', html)
        if not m:
            m = re.search(r'(magnet:\?xt=urn:btih:[0-9a-fA-F]{16,})', html)
        if m:
            magnet = m.group(1).replace('&amp;', '&')
        # 海报：详情页 <img class="rounded-2" ... src="...">（列表页无 src，只在 CSS background）
        pic = ''
        mp = re.search(r'<img[^>]*class="rounded-2"[^>]*src="([^"]+)"', html)
        if not mp:
            mp = re.search(r'<img[^>]*src="([^"]+)"[^>]*alt="[^"]*"', html)
        if not mp:
            mp = self.RE_BG.search(html)
        if mp:
            pic = mp.group(1)
        if pic:
            self._mem_pic(vid, pic)
        else:
            pic = self._mem_get(vid)
        # 在线播放：详情页 iframe 指向 /e/extend/jx.php?id={数字}，里面 var vurl='明文m3u8'
        jx_url = ''
        mj = re.search(r'/e/extend/jx\.php\?id=(\d+)', html)
        if mj:
            jx_url = '%s/e/extend/jx.php?id=%s' % (self.host, mj.group(1))
        froms, eps = [], []
        if magnet:
            froms.append('磁力')
            eps.append('磁力$' + magnet)
        if jx_url:
            froms.append('在线')
            eps.append('在线$' + jx_url)
        if not eps:
            froms.append('页面')
            eps.append('页面$' + url)
        if jx_url:
            froms.append('嗅探')
            eps.append('嗅探$' + jx_url)
        while len(eps) < len(froms):
            eps.append(eps[-1])
        remarks = (score + '分') if score else '磁力'
        if name != vid:
            self._mem_name(vid, name)
        vod = {'vod_id': vid, 'vod_name': name, 'vod_pic': pic,
               'vod_actor': actor, 'vod_director': '', 'vod_duration': length,
               'vod_year': year or '', 'vod_area': area or '', 'vod_class': genre or '',
               'vod_remarks': remarks, 'vod_content': intro,
               'vod_play_from': '$$$'.join(froms),
               'vod_play_url': '#'.join(eps)}
        return {'list': [vod]}

    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg)
            if pg < 1:
                pg = 1
        except Exception:
            pg = 1
        key = str(key or '').strip()
        if not key:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 0, 'total': 0}
        try:
            html = self._search(key)
        except Exception as e:
            return {'list': [self._diag('搜索异常 %s: %s' % (type(e).__name__, e))],
                    'page': pg, 'pagecount': 1, 'limit': 1, 'total': 1}
        vods = self._cards(html)
        if not vods:
            return {'list': [self._diag('搜索「%s」无结果' % key)],
                    'page': pg, 'pagecount': 1, 'limit': 1, 'total': 1}
        return {'list': vods, 'page': pg, 'pagecount': 1,
                'limit': len(vods), 'total': len(vods)}

    def playerContent(self, flag, id, vipFlags):
        pid = str(id or '')
        flag = str(flag or '')
        hdr = {'User-Agent': self.UA, 'Referer': self.host + '/'}
        # 在线线路：/e/extend/jx.php?id=N 内 var vurl='明文m3u8'
        if 'jx.php' in pid or '在线' in flag:
            url = pid.split('$')[-1]
            html = self._get(url)
            mv = re.search(r"vurl\s*=\s*['\"]([^'\"]+)['\"]", html or '')
            if not mv:
                mv = re.search(r'([\w:/.?=&%\-]+\.m3u8[^"\'\s<>]*)', html or '')
            if mv:
                return {'parse': 0, 'url': mv.group(1), 'header': hdr}
            return {'parse': 0, 'url': url, 'header': hdr}
        url = ''
        if pid.startswith('magnet:') or pid.startswith('ed2k:') or pid.startswith('thunder:'):
            url = pid.replace('&amp;', '&').split('&x=1')[0]
        elif pid.startswith('http'):
            url = pid
        elif pid:
            url = urljoin(self.host, pid if pid.startswith('/') else '/movie/' + pid + '.html')
        return {'parse': 0, 'url': url, 'header': hdr}

    # ---------------- 解析 ----------------
    def _pid(self, ids):
        try:
            if isinstance(ids, (list, tuple)):
                return str(ids[0].get('id') if isinstance(ids[0], dict) else ids[0])
            if isinstance(ids, dict):
                return str(ids.get('id', ''))
            return str(ids)
        except Exception:
            return ''

    def _mem_get(self, vid):
        return _MEM_PIC.get(str(vid), '')

    def _mem_pic(self, vid, pic):
        if pic:
            _MEM_PIC[str(vid)] = pic

    def _mem_name(self, vid, name):
        _MEM_NAME[str(vid)] = name

    def _cards(self, html):
        out = []
        seen = set()
        if not html:
            return out
        for m in self.RE_ITEM.finditer(html):
            kind, vid, blk = m.group(1), m.group(2), m.group(3)
            img = self.RE_BG.search(blk)
            pic = img.group(1) if img else ''
            t = re.search(r'<h2[^>]*>([^<]+)</h2>', blk)
            name = self._clean(t.group(1)) if t else ''
            if not name:
                continue
            if vid in seen:
                continue
            seen.add(vid)
            sc = re.search(r'class="rank[^"]*"[^>]*>([\d.]+)', blk)
            yr = re.search(r'<li class="d-flex align-items-center small">\s*([\d]{4})', blk)
            remark = ''
            if yr:
                remark = yr.group(1)
            elif sc:
                remark = sc.group(1)
            if pic:
                _MEM_PIC['%s@%s' % (kind, vid)] = pic
            out.append({'vod_id': '%s@%s' % (kind, vid), 'vod_name': name,
                        'vod_pic': pic, 'vod_remarks': remark})
        return out

    def _list_url(self, tid, pg):
        for t, n, base, tmpl in self.CLASSES:
            if t == tid:
                if not tmpl:
                    return self.host + base
                if pg <= 1:
                    return self.host + base
                return self.host + tmpl % pg
        # 未知 tid：兼容完整 URL 或 /s/xxx/ 专题
        if tid.startswith('http'):
            return tid
        return self.host + '/' + tid.strip('/') + ('/index_%d.html' % pg if pg > 1 else '/')

    def _pagecount(self, html, pg):
        cands = []
        for m in self.RE_PAGE.finditer(html or ''):
            try:
                cands.append(int(m.group(1)))
            except Exception:
                pass
        cands = [c for c in cands if 1 < c < 100000]
        return max(int(pg), max(cands) if cands else int(pg))

    def _detail_name(self, html):
        m = re.search(r'<div class="mv_detail[^"]*"[^>]*>.*?<h1[^>]*>([^<]+)</h1>', html, flags=re.S)
        if m:
            return self._clean(m.group(1))
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        if m:
            return self._clean(m.group(1))
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            t = m.group(1)
            t = re.sub(r'[-（(].*?(在线观看|磁力下载|下载).*$', '', t)
            t = re.sub(r'\d{4}.*$', '', t).strip(' -（()')
            return self._clean(t)
        return ''

    def _f(self, pat, txt):
        m = re.search(pat, txt or '')
        return (m.group(1).strip() if m else '')

    def _text(self, html):
        t = re.sub(r'<script.*?</script>', ' ', html or '', flags=re.S | re.I)
        t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S | re.I)
        t = re.sub(r'<[^>]+>', '|', t)
        t = t.replace('&nbsp;', ' ').replace('&amp;', '&')
        return re.sub(r'[ \t\r\n]+', ' ', t)

    def _clean(self, s):
        s = str(s or '').replace('&amp;', '&')
        s = re.sub(r'[\[\]【】]', '', s)
        s = s.replace('$', '').replace('#', '')
        return re.sub(r'\s+', ' ', s).strip()

    def _diag(self, msg):
        return {'vod_id': 'diag', 'vod_name': '[诊断] ' + msg,
                'vod_pic': '', 'vod_remarks': '诊断'}

    # ---------------- 网络 ----------------
    def _search(self, key):
        """POST 搜索表单 → 302 到 /e/search/result/?searchid=N，该页可直接 GET"""
        url = self.host + '/e/search/index.php'
        data = {'classid': '1,2', 'show': 'title', 'tempid': '1', 'keyboard': key}
        html = self._post(url, data)
        if html and self.RE_ITEM.search(html):
            return html
        m = re.search(r'/e/search/result/\?searchid=(\d+)', html or '')
        if m:
            html2 = self._get('%s/e/search/result/?searchid=%s' % (self.host, m.group(1)))
            if html2:
                return html2
        if html and self.RE_ITEM.search(html):
            return html
        return html or ''

    def _get(self, url):
        if url in _CACHE and time.time() - _CACHE[url][0] < _CACHE[url][1]:
            return _CACHE[url][2]
        txt = self._raw(url)
        if txt and len(txt) > 2000:
            _CACHE[url] = (time.time(), 300, txt)
            if len(_CACHE) > 300:
                _CACHE.clear()
        return txt

    def _post(self, url, data):
        body = '&'.join('%s=%s' % (quote(str(k)), quote(str(v))) for k, v in data.items())
        sess = self._session()
        if sess is not None:
            try:
                r = sess.post(url, data=data, headers=self._hdr(), timeout=20, allow_redirects=True)
                if r.status_code == 200 and r.text:
                    r.encoding = 'utf-8'
                    return r.text
            except Exception:
                pass
        try:
            import urllib.request, urllib.parse
            req = urllib.request.Request(url, data=body.encode('utf-8'), headers=self._hdr())
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(_cj()))
            r = op.open(req, timeout=20)
            txt = r.read().decode('utf-8', 'ignore')
            if txt:
                return txt
        except Exception:
            pass
        return ''

    def _raw(self, url):
        sess = self._session()
        if sess is not None:
            for _ in range(3):
                try:
                    r = sess.get(url, headers=self._hdr(), timeout=20)
                    if r.status_code == 200 and r.text:
                        r.encoding = 'utf-8'
                        return r.text
                except Exception:
                    pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=self._hdr())
            op = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(_cj()))
            for _ in range(3):
                try:
                    r = op.open(req, timeout=20)
                    return r.read().decode('utf-8', 'ignore')
                except Exception:
                    continue
        except Exception:
            pass
        return ''

    def _session(self):
        if self._sess is None:
            try:
                import requests
                s = requests.Session()
                s.headers.update({'User-Agent': self.UA})
                self._sess = s
            except Exception:
                self._sess = False
        return self._sess or None

    def _hdr(self):
        return {'User-Agent': self.UA,
                'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Referer': self.host + '/'}


_CACHE = {}
_MEM_PIC = {}
_MEM_NAME = {}

def _cj():
    global _CJ
    if _CJ is None:
        try:
            import http.cookiejar
            _CJ = http.cookiejar.CookieJar()
        except Exception:
            _CJ = False
    return _CJ or None

_CJ = None