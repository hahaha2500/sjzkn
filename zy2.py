# -*- coding: utf-8 -*-
"""
追影 zhuiying3.cc —— TVBox / FongMi type=3 Python 源
站型：MacCMS v10 魔改 + 自写模板（采集接口已关，纯 HTML 直抓）
列表：官方 AJAX 瀑布流 /index.php/ajax/vod_list（id,class,area,year,by,page,limit）
播放：/play/{code}-{sid}-{nid}.html → window.MAC_PLAY_CONFIG(baseKey,requestUrl)
      → POST /player_api.php  token=md5(baseKey+timestamp+UA)
      → res.data 反序 base64 → {jmurl,urltype} → 真实 m3u8
"""
import base64
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as _B
except Exception:
    _B = object

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HOSTS = ['zhuiying3.cc', 'zhuiying2.cc', 'zhuiying1.cc', 'zhuiying4.cc']
CLASSES = [('1', 'dianying', '电影'), ('2', 'dianshiju', '电视剧'), ('3', 'zongyi', '综艺'),
           ('4', 'dongman', '动漫'), ('26', 'duanju', '短剧')]
TID = {'dianying': '1', 'dianshiju': '2', 'zongyi': '3', 'dongman': '4', 'duanju': '26'}
NUM = {'1': 'dianying', '2': 'dianshiju', '3': 'zongyi', '4': 'dongman', '26': 'duanju'}
BY = {'综合排序': '', '最新上线': 'id', '热度最高': 'hits_week', '最好评': 'douban_score'}
_C = {}
_RE = {
    'tid': re.compile(r'id="wf_type_id" value="(\d+)"'),
    'maxp': re.compile(r'id="wf_max_page" value="(\d+)"'),
    'cfg': re.compile(r'baseKey:\s*"([^"]+)"'),
    'ru': re.compile(r'requestUrl:\s*"([^"]+)"'),
    'h1': re.compile(r'<h1 class="detail-title">([^<]+)</h1>'),
    'poster': re.compile(r'<img class="detail-poster" src="([^"]+)"'),
    'badge': re.compile(r'class="poster-badge">([^<]+)<'),
    'year': re.compile(r'class="detail-year">\((\d{4})\)'),
    'score': re.compile(r'class="m-val m-val-highlight"[^>]*>([\d.]+)<'),
    'genre': re.compile(r'class="m-val">([^<]{1,40})</span>\s*<span class="m-lbl">类型'),
    'area': re.compile(r'class="m-val">([^<]{1,40})</span>\s*<span class="m-lbl">制片国家'),
    'dur': re.compile(r'class="m-val">([^<]{1,20})</span>\s*<span class="m-lbl">片长'),
    'actor': re.compile(r'主演:</span>\s*<div class="val actor-val-box"[^>]*>(.*?)</div>', re.S),
    'dir': re.compile(r'导演:</span>\s*<span class="val actor-val-box">(.*?)</span>', re.S),
    'syn': re.compile(r'id="synopsisContent">(.*?)</div>', re.S),
}


def _strip(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()


def _clean(s):
    return _strip(s).replace('$', '').replace('#', '').replace('@@', '')


class Spider(_B):
    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.host = HOSTS[0]

    def getName(self):
        return '追影'

    def getDependence(self):
        return []

    def init(self, extend=''):
        e = extend
        if not isinstance(e, dict):
            try:
                e = json.loads(extend) if extend else {}
            except Exception:
                e = {}
        if isinstance(e, dict) and e.get('host'):
            h = str(e['host'])
            for p in ('https://', 'http://'):
                if h.startswith(p):
                    h = h[len(p):]
            h = h.strip('/').split('/')[0]
            if h:
                self.host = h

    # ---------------- 网络 ----------------
    def _get(self, path, ref='', tries=3):
        for host in [self.host] + [x for x in HOSTS if x != self.host]:
            for _ in range(tries):
                try:
                    rq = urllib.request.Request('https://' + host + path, headers={
                        'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*',
                        'Accept-Language': 'zh-CN,zh;q=0.9',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Referer': 'https://' + host + (ref or '/')})
                    with urllib.request.urlopen(rq, timeout=18) as r:
                        return r.read().decode('utf-8', 'ignore')
                except Exception:
                    time.sleep(0.4)
        return ''

    # ---------------- 列表解析 ----------------
    def _cards(self, html):
        out, seen = [], set()
        pat = re.compile(r'<a href="/video/(\w+)\.html" class="card js-card-item">(.*?)(?=<a href="/video/|\Z)', re.S)
        for m in pat.finditer(html or ''):
            vid, body = m.group(1), m.group(2)
            if vid in seen:
                continue
            pm = re.search(r'<div class="card-cover">.*?<img src="(https?://[^"]+)"', body, re.S)
            tm = re.search(r'<div class="card-title">([^<]{1,80})</div>', body)
            sm = re.search(r'class="card-status">([^<]{0,30})<', body)
            if not pm or not tm:
                continue
            seen.add(vid)
            out.append({'vod_id': vid, 'vod_name': _clean(tm.group(1)),
                        'vod_pic': pm.group(1),
                        'vod_remarks': _clean(sm.group(1)) if sm else ''})
        return out

    def _ajax(self, tid, pg, extend):
        q = {'id': tid, 'class': '', 'area': '', 'year': '', 'by': '', 'page': pg, 'limit': 27}
        if isinstance(extend, dict):
            for k, v in extend.items():
                if k in q and v not in (None, ''):
                    q[k] = v
        url = '/index.php/ajax/vod_list?' + urllib.parse.urlencode(q)
        raw = self._get(url, '/vodshow/%s-----------.html' % NUM.get(tid, 'dianying'))
        try:
            r = json.loads(raw)
        except Exception:
            return None
        if not isinstance(r, dict) or r.get('code') != 1:
            return None
        lst = []
        for it in (r.get('list') or []):
            vid = str(it.get('vod_url') or '')
            vid = vid.split('/video/')[-1].replace('.html', '').strip()
            if not vid:
                continue
            lst.append({'vod_id': vid, 'vod_name': _clean(str(it.get('vod_name') or '')),
                        'vod_pic': it.get('vod_pic') or '',
                        'vod_remarks': _clean(str(it.get('vod_remarks') or ''))})
        return lst, int(r.get('pagecount') or 1), int(r.get('page') or pg)

    def _norm_tid(self, tid):
        t = str(tid or '').strip()
        if t in TID:
            return TID[t], t
        if t in NUM:
            return t, NUM[t]
        for k, v in NUM.items():
            if v in t or t == k:
                return v, k
        return 'dianying', '1'

    def _filters(self, slug):
        key = 'flt:%s:%s' % (self.host, slug)
        v = _C.get(key)
        if v and time.time() - v[0] < 3600:
            return v[1]
        h = self._get('/vodshow/%s-----------.html' % slug)
        f = {'题材': [], '地区': [], '年份': [], '排序': []}
        seen = set()
        # 站点筛选位段规则（MacCMS 12 位）：
        #   slug - 类(第2段) - 区(第3段) - 排序(第4段) ... 第12段=年份
        # 直接按"第2段是否URL编码=题材 / 第3段是否URL编码=地区 / 第12段是否URL编码=年份"判定
        # 位段(实测)：seg0=slug, seg1=地区, seg2=排序, seg3=题材, seg11=年份
        area_pos, by_pos, cls_pos, year_pos = 1, 2, 3, 11
        for m in re.finditer(r'<a[^>]+href="/vodshow/([^"]+)\.html"[^>]*>([^<]{1,20})</a>', h or ''):
            u, n = m.group(1), _strip(m.group(2))
            if not n or n in ('筛选', '全部分类'):
                continue
            if u in NUM.values():
                continue
            seg = u.split('-')
            enc = lambda i: len(seg) > i and bool(re.search(r'%[0-9A-Fa-f]{2}', seg[i]))
            if len(seg) != 12:
                continue
            if n in BY:
                k = '排序'
            elif enc(cls_pos):
                k = '题材'
            elif enc(area_pos):
                k = '地区'
            elif enc(year_pos) or re.match(r'^(19|20)\d{2}$', seg[year_pos]):
                k = '年份'
            elif seg[by_pos] in ('hits_week', 'id', 'douban_score'):
                k = '排序'
            else:
                continue
            if (k, n) in seen:
                continue
            seen.add((k, n))
            f.setdefault(k, []).append({'n': n, 'v': n})
        for k in list(f.keys()):
            if not f[k]:
                del f[k]
        if '题材' not in f:
            f['题材'] = [{'n': '悬疑', 'v': '悬疑'}, {'n': '剧情', 'v': '剧情'},
                        {'n': '爱情', 'v': '爱情'}, {'n': '喜剧', 'v': '喜剧'}]
        if '地区' not in f:
            f['地区'] = [{'n': '中国大陆', 'v': '中国大陆'}, {'n': '中国香港', 'v': '中国香港'},
                        {'n': '美国', 'v': '美国'}, {'n': '日本', 'v': '日本'}, {'n': '韩国', 'v': '韩国'}]
        if '年份' not in f:
            g = time.gmtime().tm_year
            f['年份'] = [{'n': str(y), 'v': str(y)} for y in range(g, g - 12, -1)]
        f['排序'] = [{'n': n, 'v': (BY[n] or '')} for n in ('综合排序', '最新上线', '热度最高', '最好评')]
        _C[key] = (time.time(), f)
        return f

    # ---------------- 播放 ----------------
    def _play(self, code, sid, nid):
        page = '/play/%s-%s-%s.html' % (code, sid, nid)
        html = self._get(page)
        if not html:
            return ''
        k = _RE['cfg'].search(html)
        u = _RE['ru'].search(html)
        if not k or not u:
            return ''
        ts = int(time.time())
        tok = hashlib.md5((k.group(1) + str(ts) + UA).encode('utf-8')).hexdigest()
        body = urllib.parse.urlencode({'url': u.group(1), 'timestamp': ts, 'token': tok}).encode()
        try:
            rq = urllib.request.Request('https://' + self.host + '/player_api.php', data=body, headers={
                'User-Agent': UA, 'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Referer': 'https://' + self.host + page})
            with urllib.request.urlopen(rq, timeout=18) as r:
                j = json.loads(r.read().decode('utf-8', 'ignore'))
        except Exception:
            return ''
        d = j.get('data') if isinstance(j, dict) else ''
        if not d:
            return ''
        try:
            s = base64.b64decode(str(d)[::-1] + '=' * (-len(str(d)) % 4)).decode('utf-8', 'ignore')
            o = json.loads(urllib.parse.unquote(s))
        except Exception:
            return ''
        return str(o.get('jmurl') or o.get('url') or '')

    # ---------------- 六接口 ----------------
    def homeContent(self, filter=True):
        out = {'class': [{'type_id': i, 'type_name': n} for i, s, n in CLASSES]}
        key = 'home:' + self.host
        v = _C.get(key)
        if v and time.time() - v[0] < 300:
            out['list'] = v[1]
        else:
            try:
                out['list'] = self._cards(self._get('/'))
            except Exception:
                out['list'] = []
            _C[key] = (time.time(), out['list'])
        if filter:
            try:
                out['filters'] = {i: self._filters(s) for i, s, n in CLASSES}
            except Exception:
                pass
        return out

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        num, slug = self._norm_tid(tid)
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        r = None
        try:
            r = self._ajax(num, pg, extend if filter else {})
        except Exception:
            r = None
        if r and r[0]:
            lst, pc, cp = r
            return {'list': lst, 'page': cp, 'pagecount': pc, 'limit': 27,
                    'total': pc * 27, 'filter': self._filters(slug)}
        h = self._get('/vodshow/%s%s.html' % (slug, '--------%d---' % pg if pg > 1 else '-----------'))
        lst = self._cards(h)
        pc = pg + 1
        m = _RE['maxp'].search(h or '')
        if m:
            pc = min(int(m.group(1)), 20000)
        elif not lst:
            pc = pg
        try:
            flt = self._filters(slug)
        except Exception:
            flt = {}
        return {'list': lst, 'page': pg, 'pagecount': pc, 'limit': 27, 'total': pc * 27, 'filter': flt}

    def detailContent(self, ids):
        vid = ''
        try:
            if isinstance(ids, (list, tuple)):
                vid = str(ids[0]) if ids else ''
            else:
                vid = str(ids or '')
        except Exception:
            vid = ''
        h = self._get('/video/%s.html' % vid)
        if not h:
            return {'list': [{'vod_id': vid, 'vod_name': vid, 'vod_pic': '',
                              'vod_play_from': '追影', 'vod_play_url': '正片$%s@@2@@1' % vid}]}
        tabs = list(re.finditer(r'data-target="ep-list-(\d+)">(.*?)(?=<div class="source-tab"|</div>\s*</div>\s*</div>)', h, re.S))
        blocks = {}
        for mm in re.finditer(r'<div class="ep-square-list" id="ep-list-(\d+)"[^>]*data-total="(\d+)"[^>]*>', h):
            st = mm.end()
            nxt = re.search(r'<div class="ep-square-list" id="ep-list-\d+"', h[st:])
            blk = h[st:st + (nxt.start() if nxt else 200000)]
            # 末尾可能被"相关推荐"污染，只取连续 <a> 区段
            cut = blk.find('<div class="ep-drawer')
            if cut > 0:
                blk = blk[:cut]
            blocks[mm.group(1)] = blk
        lines, froms = [], []
        for t in tabs:
            sid = t.group(1)
            blk = blocks.get(sid, '')
            nm = re.search(r'tab-name">([^<]*)<', t.group(2))
            eps = []
            for e in re.finditer(r'href="/play/%s-%s-(\d+)\.html"[^>]*>(.*?)</a>' % (re.escape(vid), re.escape(sid)), blk, re.S):
                nid = e.group(1)
                lbl = _clean(e.group(2)) or nid
                if not re.match(r'^\d+$', nid):
                    continue
                eps.append('第%s集$%s' % (nid, '%s@@%s@@%s' % (vid, sid, nid)))
            if not eps:
                continue
            lines.append('#'.join(eps))
            froms.append(_strip(nm.group(1)) if nm else sid)
        def g(pat):
            mm = pat.search(h)
            return _clean(mm.group(1)) if mm else ''
        p = _RE
        v = {'vod_id': vid,
             'vod_name': g(p['h1']) or vid,
             'vod_pic': g(p['poster']),
             'vod_year': g(p['year']),
             'vod_area': g(p['area']),
             'vod_remarks': g(p['badge']),
             'vod_score': g(p['score']),
             'vod_actor': g(p['actor']),
             'vod_director': g(p['dir']),
             'vod_class': g(p['genre']),
             'vod_duration': g(p['dur']),
             'vod_content': g(p['syn'])}
        if lines:
            v['vod_play_from'] = '$$$'.join(froms)
            v['vod_play_url'] = '$$$'.join(lines)
        else:
            v['vod_play_from'] = '追影'
            v['vod_play_url'] = '正片$%s@@2@@1' % vid
        return {'list': [v]}

    def searchContent(self, key, quick, pg='1', flt=None):
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        q = {'wd': str(key), 'tid': '', 'page': pg, 'limit': 20}
        if isinstance(flt, dict) and flt.get('tid'):
            q['tid'] = extend['tid']
        raw = self._get('/index.php/ajax/search_list?' + urllib.parse.urlencode(q), '/')
        try:
            r = json.loads(raw)
        except Exception:
            r = None
        if isinstance(r, dict) and r.get('code') == 1:
            out = []
            for it in (r.get('list') or []):
                vid = str(it.get('vod_url') or '')
                vid = vid.split('/video/')[-1].replace('.html', '').strip()
                if not vid:
                    continue
                out.append({'vod_id': vid, 'vod_name': _clean(str(it.get('vod_name') or '')),
                            'vod_pic': it.get('vod_pic') or '',
                            'vod_remarks': _clean(str(it.get('vod_remarks') or '')),
                            'vod_year': str(it.get('vod_year') or ''),
                            'vod_area': str(it.get('vod_area') or '')})
            return {'list': out, 'page': int(r.get('page') or pg), 'pagecount': int(r.get('pagecount') or 1),
                    'limit': 20, 'total': int(r.get('pagecount') or 1) * 20}
        h = self._get('/index.php/vod/search.html?wd=' + urllib.parse.quote(str(key)))
        out, seen = [], set()
        if h:
            body = h[h.find('<div class="search-list">'):]
            if body.find('hotsearch_hot_item') > 0:
                body = body[:body.find('hotsearch_hot_item')]
            blocks = re.split(r'<div class="search-hero-card|search-item-card|class="result-card', body)
            for b in blocks:
                m1 = re.search(r'href="/video/(\w+)\.html"', b)
                m2 = re.search(r'<img src="(https?://[^"]+)"', b)
                nm = None
                for pat in (r'class="search-hero-title"><a[^>]*>([^<]{1,80})<',
                            r'class="hero-thumb"[^>]*title="([^"]{1,80})"',
                            r'<div class="card-title">([^<]{1,80})<',
                            r'class="item-title"[^>]*>([^<]{1,80})<'):
                    mm = re.search(pat, b)
                    if mm:
                        nm = _clean(mm.group(1))
                        break
                if not (m1 and nm):
                    continue
                vid = m1.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                rm = re.search(r'class="(?:hero-thumb-remark|card-status|item-remark)">([^<]{0,30})<', b)
                out.append({'vod_id': vid, 'vod_name': nm,
                            'vod_pic': m2.group(1) if m2 else '',
                            'vod_remarks': _clean(rm.group(1)) if rm else ''})
        return {'list': out, 'page': pg, 'pagecount': 1, 'limit': len(out) or 30, 'total': len(out)}

    def playerContent(self, flag, id, vipFlags):
        code, sid, nid = '', '2', '1'
        try:
            p = [x for x in str(id).split('@@') if x != '']
            if p:
                code = p[0]
                sid = p[1] if len(p) > 1 else '2'
                nid = p[2] if len(p) > 2 else '1'
        except Exception:
            pass
        url = ''
        if code:
            for _ in range(2):
                try:
                    url = self._play(code, sid, nid)
                except Exception:
                    url = ''
                if url:
                    break
                time.sleep(0.5)
        head = {'User-Agent': UA, 'Referer': 'https://' + self.host + '/'}
        if url:
            return {'parse': 0, 'playUrl': '', 'url': url, 'header': head}
        if code:
            return {'parse': 0, 'playUrl': '', 'url': 'https://' + self.host + '/play/%s-%s-%s.html' % (code, sid, nid),
                    'header': head}
        return {'parse': 0, 'playUrl': '', 'url': '', 'header': head}

    def localProxy(self, param=None):
        return [404, 'text/plain', b'', {'User-Agent': UA}]

    def isVideoFormat(self, url):
        return any(x in (url or '').lower() for x in ('.m3u8', '.mp4', '.flv', '.ts'))
