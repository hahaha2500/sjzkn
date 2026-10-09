# -*- coding: utf-8 -*-
"""
mp4ba（高清Mp4吧）TVBox/FongMi type=3 源
站型: WordPress + Loostrive 主题, 纯磁力/BT下载站(无在线流)
特色: 首访403下发cookie自跳(同会话重试即通), 搜索有算术验证码
"""
import re, json, time, random
from urllib.parse import quote, urljoin

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def __init__(self):
            pass


def _newsess():
    try:
        import requests
        from requests.adapters import HTTPAdapter
        try:
            from requests.packages.urllib3.util.retry import Retry
        except Exception:
            from urllib3.util.retry import Retry
        _S = requests.Session()
        _S.trust_env = True
        _S.mount('http://', HTTPAdapter(max_retries=0, pool_connections=20, pool_maxsize=40))
        _S.mount('https://', HTTPAdapter(max_retries=0, pool_connections=20, pool_maxsize=40))
        return _S
    except Exception:
        return None


_SESS = None
_CACHE = {}
_PROXIES = None


class Spider(BaseSpider):
    HOSTS = ['https://www.mp4ba.vip']
    NAME = '高清MP4吧'
    UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

    CLASSES = [
        ('recommend', '必看推荐'),
        ('american-movies', '欧美电影'),
        ('domestic-films', '国产电影'),
        ('japan-and-south-korea-movie', '日韩电影'),
        ('animated-movie', '动画电影'),
        ('hong-kong-and-taiwan-movies', '港台电影'),
        ('overseas-movies', '海外电影'),
        ('european-and-american-tv-series', '欧美电视剧'),
        ('documentary', '纪录片'),
    ]

    PAGE_RE = re.compile(r'/page/(\d+)/?["\']')
    PAGE_MAX_RE = re.compile(r'/page/(\d+)/?["\'<]')

    def __init__(self, extend=''):
        try:
            BaseSpider.__init__(self)
        except Exception:
            pass
        self.extend = extend
        self.host = self.HOSTS[0]
        self.ext = {}
        try:
            if isinstance(extend, dict):
                self.ext = extend
            elif isinstance(extend, list):
                self.ext = extend[0] if extend else {}
            elif isinstance(extend, str) and extend.strip():
                t = extend.strip()
                if t.startswith('{'):
                    self.ext = json.loads(t)
                elif t.startswith('http'):
                    try:
                        self.ext = json.loads(self._raw(t))
                    except Exception:
                        self.ext = {}
        except Exception:
            self.ext = {}
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
                       ('.m3u8', '.mp4', '.flv', '.ts', '.mkv', '.mp4', '.avi', '.mpg'))
        except Exception:
            return False

    def manualVideoCheck(self):
        return False

    def homeContent(self, filter):
        v = self._get(self.host + '/')
        cls = [{'type_id': t, 'type_name': n} for t, n in self.CLASSES]
        vods = self._cards(v)
        return {'class': cls, 'filters': self._filters(), 'list': vods,
                'page': 1, 'pagecount': 1, 'limit': len(vods), 'total': len(vods)}

    def homeVideoContent(self):
        v = self._get(self.host + '/')
        vods = self._cards(v)
        return {'list': vods, 'page': 1, 'pagecount': 1,
                'limit': len(vods), 'total': len(vods)}

    def _filters(self):
        cls = [{'type_id': t, 'type_name': n} for t, n in self.CLASSES]
        return {'class': cls}

    def categoryContent(self, tid, pg, filter, extend):
        tid = str(tid or '').strip()
        pg = self._pg(pg)
        if pg == 1:
            url = '%s/category/%s' % (self.host, tid)
        else:
            url = '%s/category/%s/page/%d' % (self.host, tid, pg)
        v = self._get(url)
        vods = self._cards(v)
        pc = self._pagecount(v, pg)
        return {'list': vods, 'page': pg, 'pagecount': pc, 'limit': len(vods), 'total': len(vods) * pc}

    def detailContent(self, ids):
        vid = self._pid(ids)
        v = self._get('%s/item/%s' % (self.host, vid))
        if not v:
            return {'list': [{'vod_id': vid, 'vod_name': vid, 'vod_play_from': 'MP4吧·磁力',
                              'vod_play_url': '磁力$' + vid}]}
        title = self._detail_title(v) or vid
        txt = self._text(v)
        name = self._f(r'◎\s*标\s*题\s*(?:</?[^>]+>\s*)*([^◎<]{1,60})', txt) or title
        year = self._f(r'◎\s*年\s*代\s*(?:</?[^>]+>\s*)*(\d{4})', txt)
        area = self._f(r'◎\s*产\s*地\s*(?:</?[^>]+>\s*)*([^◎<]{1,30})', txt)
        genre = self._f(r'◎\s*类\s*别\s*(?:</?[^>]+>\s*)*([^◎<]{1,60})', txt)
        intro = self._f(r'◎\s*简\s*介\s*(?:</?[^>]+>\s*)*([^◎]{2,600})', txt)
        magnet = ''
        m = re.search(r'data-clipboard-text="(magnet:\?[^"]+)"', v)
        if not m:
            m = re.search(r'(magnet:\?xt=urn:btih:[0-9a-fA-F]{20,})', v)
        if m:
            magnet = m.group(1).replace('&amp;', '&')
        torrent = ''
        m2 = re.search(r'(https?://[^\s"\'<>]+?\.torrent)', v)
        if not m2:
            m2 = re.search(r'href="(/\?dl_id=\d+)"', v)
        if m2:
            torrent = m2.group(1) if m2.group(1).startswith('/') else m2.group(1)
        pic = ''
        for m3 in re.finditer(r'<div class="post-thumb[^"]*"[^>]*>\s*<img[^>]+src="([^"]+)"', v):
            pic = m3.group(1)
            break
        if not pic:
            m3 = re.search(r'<meta property="og:image" content="([^"]+)"', v)
            if m3:
                pic = m3.group(1)
        if not pic:
            # 详情页自身无缩略图, 取正文第一张图(站点海报图)
            for m3 in re.finditer(r'(https?://|/)[^"\'\s<>]*?/wp-content/uploads/[^"\'\s<>]+\.(?:jpg|jpeg|png|webp)', v):
                pic = m3.group(0)
                break
        froms = []
        eps = []
        if magnet:
            froms.append('磁力'); eps.append('磁力$' + magnet)
        if torrent:
            froms.append('种子'); eps.append('种子$' + urljoin(self.host, torrent))
        if not eps:
            froms.append('资源'); eps.append('资源$' + self.host + '/item/' + vid)
        pic = self._pic(pic) or self._mem_get(vid)
        if not pic:
            pic = ''
        if eps:
            self._mem_pic(vid, pic)
        else:
            pic = ''
        vod = {'vod_id': vid, 'vod_name': name, 'vod_pic': pic,
               'vod_year': year, 'vod_area': area, 'vod_class': genre,
               'vod_remarks': '下载', 'vod_content': intro,
               'vod_play_from': '$$$'.join(froms),
               'vod_play_url': '#'.join(eps)}
        return {'list': [vod]}

    def searchContent(self, key, quick, pg):
        pg = self._pg(pg)
        key = str(key or '').strip()
        if not key:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 0, 'total': 0}
        url = '%s/?s=%s' % (self.host, quote(key))
        v = self._search_html(url)
        vods = self._cards(v)
        if len(vods) < 3:
            time.sleep(1.0)
            ks = re.sub(r'\s+', '', key)
            v2 = self._search_html('%s/?s=%s' % (self.host, quote(ks)))
            v2c = self._cards(v2)
            if len(v2c) > len(vods):
                v = v2; vods = v2c
        pc = self._pagecount(v, pg) if vods else 1
        return {'list': vods, 'page': pg, 'pagecount': pc, 'limit': len(vods), 'total': len(vods) * pc}

    def playerContent(self, flag, id, vipFlags):
        pid = str(id or '')
        url = ''
        hdr = {'User-Agent': self.UA}
        if pid.startswith('magnet:') or pid.startswith('ed2k:') or pid.startswith('thunder:'):
            url = pid.replace('&amp;', '&')
        elif pid.startswith('http'):
            url = pid
        elif pid.startswith('/') and 'dl_id' in pid:
            url = urljoin(self.host, pid)
        elif pid.startswith('/item/'):
            url = urljoin(self.host, pid)
        elif pid.startswith('/'):
            url = urljoin(self.host, pid)
        else:
            url = pid
        return {'parse': 0, 'url': url, 'header': hdr}

    def localProxy(self, param=None):
        return [404, 'text/plain', b'', {}]

    def action(self, aid):
        return False

    def liveContent(self, url):
        return ''

    def isLive(self):
        return False

    def manualSearchCheck(self):
        return False

    def proxyLocal(self):
        return None

    # ---------------- internals ----------------
    def _detail_title(self, html):
        m = re.search(r'<h1[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</h1>', html, flags=re.S)
        if m:
            return self._clean(re.sub(r'<[^>]+>', ' ', m.group(1)))
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            t = m.group(1).split(' - ')[0].strip()
            return self._clean(t)
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        return self._clean(m.group(1)) if m else ''

    def _clean(self, s):
        s = str(s or '').replace('&amp;', '&')
        s = re.sub(r'[\[\]【】]', '', s)
        s = s.replace('$', '').replace('#', '')
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def _pid(self, ids):
        try:
            if isinstance(ids, (list, tuple)):
                return str(ids[0].get('id') if isinstance(ids[0], dict) else ids[0])
            if isinstance(ids, dict):
                return str(ids.get('id', ''))
            return str(ids)
        except Exception:
            return ''

    def _pg(self, pg):
        try:
            n = int(pg)
            return n if n > 0 else 1
        except Exception:
            return 1

    def _pagecount(self, html, pg):
        cands = []
        for m in self.PAGE_MAX_RE.finditer(html or ''):
            try:
                cands.append(int(m.group(1)))
            except Exception:
                pass
        cands = [c for c in cands if 1 < c < 100000]
        mx = max(cands) if cands else pg
        return max(int(pg), mx)

    def _f(self, pat, txt):
        m = re.search(pat, txt or '')
        return (m.group(1).strip() if m else '')

    def _text(self, html):
        t = re.sub(r'<script.*?</script>', ' ', html or '', flags=re.S | re.I)
        t = re.sub(r'<style.*?</style>', ' ', t, flags=re.S | re.I)
        t = re.sub(r'<[^>]+>', ' ', t)
        t = (t.replace('&nbsp;', ' ').replace('&amp;', '&'))
        return re.sub(r'\s+', ' ', t)

    def _pic(self, url):
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return urljoin(self.host, url)
        return url

    def _mem_get(self, vid):
        try:
            return _PIC.get(self.host, {}).get(str(vid), '')
        except Exception:
            return ''

    def _mem_pic(self, vid, pic):
        try:
            if pic:
                _PIC.setdefault(self.host, {})[vid] = pic
        except Exception:
            pass

    def _cards(self, html):
        out = []
        seen = set()
        if not html:
            return out
        for m in re.finditer(r'<li class="post box row[^"]*"[^>]*>(.*?)</li>', html, flags=re.S):
            blk = m.group(1)
            am = re.search(r'href="/item/(\d+)"[^>]*title="([^"]*)"', blk)
            if not am:
                am = re.search(r'href="/item/(\d+)"[^>]*>\s*<img[^>]+alt="([^"]*)"', blk)
            if not am:
                continue
            vid = am.group(1)
            name = self._clean(am.group(2))
            if not name:
                continue
            if vid in seen:
                continue
            seen.add(vid)
            img = ''
            for att in ('data-src', 'data-original', 'data-lazy-src', 'src'):
                im = re.search(r'%s="([^"]+)"' % att, blk)
                if im and im.group(1) and 'placeholder' not in im.group(1):
                    img = im.group(1)
                    break
            if img and ('/themes/' in img or 'logo' in img or '1x1' or 'spinner' in img):
                pass
            date = ''
            dm = re.search(r'info_date[^>]*>([^<]+)<', blk)
            if dm:
                date = dm.group(1).strip()
            cat = ''
            cm = re.search(r'info_category[^>]*>\s*<a[^>]*>([^<]+)<', blk)
            if cm:
                cat = cm.group(1).strip()
            out.append({'vod_id': vid, 'vod_name': name, 'vod_pic': self._pic(img),
                        'vod_remarks': date or cat or '下载'})
            if img:
                self._mem_pic(vid, self._pic(img))
        return out

    def _search_html(self, url):
        # 算术验证码: "12 + 32 = <input name=result>"
        for _ in range(8):
            v = self._get(url, raw=True)
            if not v:
                return ''
            if 'erphp-search-captcha' not in v:
                return v
            q = self._f(r'>\s*([\d\s\+\-\*x×÷\(\)]+?)\s*=\s*<input', v)
            if not q:
                q = self._f(r'(\d+\s*\+\s*\d+)', v)
            ans = self._calc(q)
            if ans is None:
                return ''
            data = {'result': str(ans)}
            v2 = self._post(url, data)
            if not v2:
                return ''
            if 'erphp-search-captcha' not in v2:
                # post 后可能跳到结果页
                if '/item/' in v2:
                    return v2
                v3 = self._get(url)
                if '/item/' in v3:
                    return v3
                return v2
        return ''

    def _calc(self, q):
        try:
            q = q.replace('×', '*').replace('x', '*').replace('X', '*').replace('÷', '/')
            if re.match(r'^\s*\d+\s*[+\-*/]\s*\d+\s*$', q):
                a, op, b = re.findall(r'(\d+)\s*([+\-*/])\s*(\d+)', q)[0]
                a = int(a); b = int(b)
                return {'+': a + b, '-': a - b, '*': a * b, '/': int(a / b) if b else 0}[op]
            if not re.match(r'^\s*[\d\+\-\*/\(\)\s]+$', q):
                return None
            return int(eval(q))
        except Exception:
            return None

    def _get(self, url, raw=False):
        ck = url
        if not raw:
            ck = url
        if ck in _CACHE and not raw:
            it = _CACHE[ck]
            if time.time() - it[0] < it[1]:
                return it[2]
        txt = self._fetch(url)
        if not raw and txt and len(txt) > 3000:
            _CACHE[ck] = (time.time(), 300, txt)
        return txt

    def _fetch(self, url):
        sess = _sess()
        if sess is None:
            return ''
        for i in range(4):
            try:
                r = sess.get(url, timeout=25, headers=self._hdr())
                if r.status_code in (301, 302):
                    try:
                        r = sess.get(urljoin(url, r.headers.get('Location', '')),
                                     timeout=25, headers=self._hdr())
                    except Exception:
                        pass
                if r.status_code == 403 and len(r.text) < 400:
                    continue
                return r.text
            except Exception:
                continue
        return ''

    def _post(self, url, data):
        sess = _sess()
        if sess is None:
            return ''
        for i in range(3):
            try:
                r = sess.post(url, data=data, timeout=25, headers=self._hdr())
                if r.status_code == 403 and len(r.text) < 400:
                    continue
                return r.text
            except Exception:
                continue
        return ''

    def _hdr(self):
        return {'User-Agent': self.UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Referer': self.host + '/'}


_PIC = {}


def _sess():
    global _SESS
    if _SESS is None:
        _SESS = _newsess()
    return _SESS