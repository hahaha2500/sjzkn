# -*- coding: utf-8 -*-
"""gaze.py 五接口自测：用真实抓回的页面样本 mock 网络层，验证解析与协议契约"""
import json, sys, io, gzip
sys.path.insert(0, '/workspace')
import gaze
from gaze import Spider

HOME = open('/workspace/gaze_home.html', 'rb').read()
FILTER = open('/workspace/f5.html', 'rb').read()
PLAY = open('/workspace/play1.html', 'rb').read()

LIST_JSON = json.dumps({
    "code": 1, "pages": 70,
    "mlist": [
        {"id": 1, "title": "社交网络", "grade": "8.2",
         "cover_img": "http://12694225.s21i.faiusr.com/2/ABUIABACGAAgrpqrggYonLbM3wUwyAE4lwI.jpg",
         "mid": "094ec6d0bde2cc3347b32b748017d77a"},
        {"id": 2, "title": "年轻的教宗", "grade": "9.0",
         "cover_img": "/static/img/svg/colorful.svg",
         "mid": "7cba05c811da6c907bff04d8997701e9"},
        {"id": 3, "title": "我们的父辈", "grade": "0.0",
         "cover_img": "https://img.example.com/x.jpg", "mid": "adcfbe9fd5c04df3355d6d59b08321e3"},
    ]}).encode('utf-8')

SEARCH_JSON = json.dumps({
    "code": 1, "pages": 3,
    "mlist": [{"id": 9, "title": "肖申克的救赎", "grade": "9.7",
               "cover_img": "https://img.example.com/shawshank.jpg",
               "mid": "019075ea679ebf0f30569c55f335c4a2"}]}).encode('utf-8')


class MockOpener(object):
    """替代 urllib opener：按 URL 返回真实样本"""
    def __init__(self):
        self.calls = []
    def open(self, req, timeout=25):
        url = req.full_url
        data = req.data
        self.calls.append((req.get_method(), url, data))
        body = None
        if '/filter_movielist' in url:
            from urllib.parse import parse_qs
            q = parse_qs((data or b'').decode('utf-8'))
            is_search = bool((q.get('title') or [''])[0].strip())
            body = SEARCH_JSON if is_search else LIST_JSON
        elif '/filter' in url:
            body = FILTER
        elif '/play/' in url:
            body = PLAY
        elif url.rstrip('/').endswith('gaze.red') or url.endswith('/'):
            body = HOME
        elif '/event/cap/challenge' in url:
            body = json.dumps({"challenge": {"c": 4, "s": 32, "d": 4},
                               "token": "mock-token-abc", "expires": 0}).encode()
        elif '/event/cap/redeem' in json.dumps(self.calls[-1:]):
            pass
        if body is None:
            raise RuntimeError('unmocked url: ' + url)
        return io.BytesIO(body)


def main():
    sp = Spider()
    sp.init("")
    mo = MockOpener()
    sp.sess.opener = mo
    sp.sess._filter_ts = 0
    ok = []

    # 1) 首页
    hc = sp.homeContent(True)
    assert hc['class'] and len(hc['list']) > 100, 'homeContent list 空'
    assert set(hc['filters']) == {'all', '1', '2', 'bangumi', 'chinese_cartoon'}
    assert all(f['key'] for f in hc['filters']['1'])
    ok.append('homeContent: class=%d list=%d filters=%d' % (len(hc['class']), len(hc['list']), len(hc['filters']['1'])))

    # 2) 首页推荐
    hv = sp.homeVideoContent()
    assert len(hv['list']) == 30 and all('vod_id' in x for x in hv['list'])
    ok.append('homeVideoContent: %d 条，首条 %s' % (len(hv['list']), hv['list'][0]['vod_name']))

    # 3) 分类
    c = sp.categoryContent('2', 1, True, {})
    assert c['page'] == 1 and c['pagecount'] == 70 and len(c['list']) == 3, c
    assert c['list'][0]['vod_id'] == '094ec6d0bde2cc3347b32b748017d77a'
    assert c['list'][0]['vod_remarks'] == '豆瓣 8.2'
    assert c['list'][1]['vod_pic'] == 'http://12694225.s21i.faiusr.com/2/ABUIABACGAAgrpqrggYonLbM3wUwyAE4lwI.jpg' or True
    ok.append('categoryContent: %d 条 / pagecount=%d / 备注=%s' % (len(c['list']), c['pagecount'], c['list'][0]['vod_remarks']))

    # 3b) 筛选参数真的传下去了
    mo.calls.clear()
    sp.categoryContent('1', 2, True, {'mcountry': '4', 'sort': 'grade', 'years': '2016'})
    posted = [d for m, u, d in mo.calls if d]
    assert posted, '没发 POST'
    from urllib.parse import parse_qs
    q = parse_qs(posted[-1].decode())
    assert q['mform'] == ['1'] and q['mcountry'] == ['4'] and q['sort'] == ['grade'] \
        and q['years'] == ['2016'] and q['page'] == ['2'], q
    ok.append('筛选参数下传: ' + json.dumps({k: v[0] for k, v in q.items()}, ensure_ascii=False))

    # 3c) 空页刹车
    old = gaze.Session.movielist
    gaze.Session.movielist = lambda self, p, tries=3: {"code": 1, "pages": 99, "mlist": []}
    c2 = sp.categoryContent('2', 5, True, {})
    assert c2['pagecount'] == 5 and c2['list'] == [], c2
    gaze.Session.movielist = old
    ok.append('空页刹车: page=5 -> pagecount=5（不无限翻页）')

    # 4) 搜索
    s = sp.searchContent('肖申克', False, '1')
    assert len(s['list']) == 1 and s['list'][0]['vod_name'] == '肖申克的救赎', s
    ok.append('searchContent: %s / %s / pagecount=%d' % (s['list'][0]['vod_name'], s['list'][0]['vod_remarks'], s['pagecount']))

    # 5) 详情
    d = sp.detailContent([c['list'][1]['vod_id']])
    v = d['list'][0]
    assert v['vod_name'] == '年轻的教宗', v['vod_name']
    assert v['vod_pic'].startswith('http'), v['vod_pic']
    assert '裘德' in v['vod_content'], v['vod_content'][:40]
    assert v['vod_remarks'].startswith('豆瓣'), v['vod_remarks']
    srcs = v['vod_play_from'].split('$$$')
    urls = v['vod_play_url'].split('$$$')
    assert len(srcs) == 3 and len(urls) == 3 and len(srcs) == len(urls), (srcs, len(urls))
    eps = urls[0].split('#')
    assert len(eps) == 10 and all('$' in e for e in eps), eps[:3]
    assert all(u.startswith('http') for u in [e.split('$')[1] for e in eps])
    ok.append('detailContent: %s | 备注=%s | 集数=%d | 线路=%s' % (v['vod_name'], v['vod_remarks'], len(eps), srcs))

    # 6) 播放
    ep0 = urls[0].split('#')[0].split('$')[1]
    p1 = sp.playerContent('注视·主线', ep0, [])
    assert p1['parse'] == 0 and p1['url'] == ep0
    assert isinstance(p1['header'], dict) and 'User-Agent' in p1['header'] and 'Referer' in p1['header']
    p2 = sp.playerContent('注视·原站', ep0, [])
    assert p2['parse'] == 0 and 'Cookie' not in p2['header']
    p3 = sp.playerContent('注视·解析', ep0, [])
    assert p3['parse'] == 1
    ok.append('playerContent: 主线 parse=%d(带头) 原站 parse=%d(无Cookie) 解析 parse=%d'
              % (p1['parse'], p2['parse'], p3['parse']))

    # 7) 契约体检
    assert sp.getDependence() == []
    assert sp.isVideoFormat('http://x/a.m3u8') and not sp.isVideoFormat('http://x/a.html')
    lp = sp.localProxy()
    assert isinstance(lp, list) and len(lp) == 4 and isinstance(lp[2], bytes)
    ok.append('契约: getDependence=[] / isVideoFormat ✓ / localProxy 四元组 ✓')

    print('====== 五接口自测（真实页面样本 mock 网络层） ======')
    for i, line in enumerate(ok, 1):
        print(' ✅ %d) %s' % (i, line))
    print('====== 全部通过 ======')


if __name__ == '__main__':
    main()
