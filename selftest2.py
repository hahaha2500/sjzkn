# -*- coding: utf-8 -*-
"""gaze2.py 自测：真实页面样本 mock 网络层 + 镜像切换/gzip 单元测试"""
import gzip
import io
import json
import sys
from urllib.parse import parse_qs

sys.path.insert(0, '/workspace')
import gaze2 as gaze
from gaze2 import Spider, Session, _proof_from_html

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

CAP_CHAL = json.dumps({"challenge": {"c": 4, "s": 32, "d": 4},
                       "token": "mock-token", "expires": 0}).encode()
CAP_REDEEM = json.dumps({"success": True, "token": "gaze-web:mock", "expires": 0}).encode()

calls = []


def fake_send(self, method, path, data=None, headers=None, timeout=20):
    """mock 掉真实网络：按 path 回真实样本，并记录请求头"""
    calls.append({"m": method, "path": path, "data": data,
                  "headers": dict(headers or {}), "host": self.host})
    if path.startswith('/filter_movielist'):
        q = parse_qs((data or b'').decode('utf-8'))
        return SEARCH_JSON if (q.get('title') or [''])[0].strip() else LIST_JSON
    if path == '/filter':
        return FILTER
    if path.startswith('/play/'):
        return PLAY
    if path == '/event/cap/challenge':
        return CAP_CHAL
    if path == '/event/cap/redeem':
        return CAP_REDEEM
    if path == '/':
        return HOME
    raise RuntimeError('unmocked path ' + path)


def attach(sp):
    sp.sess._send = lambda *a, **k: fake_send(sp.sess, *a, **k)


def main():
    sp = Spider()
    sp.init("")
    attach(sp)
    ok = []

    # 0) 基础契约
    assert sp.getDependence() == []
    assert sp.isVideoFormat('http://x/a.m3u8') and not sp.isVideoFormat('a.html')
    assert len(sp.localProxy()) == 4 and isinstance(sp.localProxy()[2], bytes)
    ok.append('契约: getName=%s / getDependence=[] / isVideoFormat ✓ / localProxy 四元组 ✓' % sp.getName())

    # 1) 首页
    hc = sp.homeContent(True)
    assert len(hc['list']) > 100 and set(hc['filters']) == {'all', '1', '2', 'bangumi', 'chinese_cartoon'}
    ok.append('homeContent: class=%d list=%d filters=%d组' % (len(hc['class']), len(hc['list']), len(hc['filters']['1'])))

    # 2) 首页推荐（应命中 5 分钟缓存，不再发请求）
    n0 = len(calls)
    hv = sp.homeVideoContent()
    assert len(hv['list']) == 30
    assert len(calls) == n0, '首页第二次调用应走缓存，实际又发了 %d 次请求' % (len(calls) - n0)
    ok.append('homeVideoContent: 30 条，且命中首页缓存（0 次新请求）')

    # 3) 分类
    c = sp.categoryContent('2', 1, True, {})
    assert c['page'] == 1 and c['pagecount'] == 70 and len(c['list']) == 3
    assert c['list'][0]['vod_remarks'] == '豆瓣 8.2'
    ok.append('categoryContent: %d 条 / pagecount=%d / 备注=%s' % (len(c['list']), c['pagecount'], c['list'][0]['vod_remarks']))

    # 3b) 筛选参数下传 + proof 头真的发出去
    expect_proof = _proof_from_html(FILTER.decode('utf-8', 'replace'))
    calls.clear()
    sp.categoryContent('1', 2, True, {'mcountry': '4', 'sort': 'grade', 'years': '2016'})
    post = [c for c in calls if c['path'].endswith('/filter_movielist')][0]
    q = parse_qs(post['data'].decode())
    got = tuple(q[k][0] for k in ('mform', 'mcountry', 'sort', 'years', 'page'))
    assert got == ('1', '4', 'grade', '2016', '2'), q
    low = dict((k.lower(), v) for k, v in post['headers'].items())
    hit = [k for k in expect_proof if k.lower() in low]
    assert hit and len(hit) == len(expect_proof), ('proof 头缺失', list(low), list(expect_proof))
    for k in hit:
        assert low[k.lower()] == expect_proof[k], (k, low[k.lower()], expect_proof[k])
    assert any(k.lower().startswith('x-gaze-') for k in hit), '随机 DOM 头没发'
    assert post['headers'].get('Referer') and post['headers']['Referer'].startswith('https://'), post['headers'].get('Referer')
    ok.append('筛选参数下传 + proof 头 %d/%d 随请求发出（含 x-gaze-*），Referer=%s'
              % (len(hit), len(expect_proof), post['headers']['Referer']))

    # 空页刹车
    real_mv = gaze.Session.movielist
    gaze.Session.movielist = lambda self, p, tries=3: {"code": 1, "pages": 99, "mlist": []}
    c2 = sp.categoryContent('2', 5, True, {})
    assert c2['pagecount'] == 5 and c2['list'] == []
    gaze.Session.movielist = real_mv
    attach(sp)
    ok.append('空页刹车: page=5 -> pagecount=5（不无限翻页）')

    s = sp.searchContent('肖申克', False, '1')
    assert s['list'] and s['list'][0]['vod_name'] == '肖申克的救赎'
    ok.append('searchContent: %s / %s / pagecount=%d' % (s['list'][0]['vod_name'], s['list'][0]['vod_remarks'], s['pagecount']))

    # 3) 详情
    d = sp.detailContent([c['list'][1]['vod_id']])
    v = d['list'][0]
    srcs = v['vod_play_from'].split('$$$')
    urls = v['vod_play_url'].split('$$$')
    eps = urls[0].split('#')
    assert v['vod_name'] == '年轻的教宗' and len(srcs) == len(urls) == 3 and len(eps) == 10
    assert all(u.startswith('https://gaze.red/play/') for u in [e.split('$')[1] for e in eps])
    ok.append('detailContent: %s | %s | 集数=%d | 线路=%s' % (v['vod_name'], v['vod_remarks'], len(eps), srcs))

    # 4) 播放
    ep0 = eps[0].split('$')[1]
    p1 = sp.playerContent('注视·主线', ep0, [])
    p2 = sp.playerContent('注视·原站', ep0, [])
    p3 = sp.playerContent('注视·解析', ep0, [])
    assert p1['parse'] == 0 and isinstance(p1['header'], dict) and 'User-Agent' in p1['header']
    assert p2['parse'] == 0 and 'Cookie' not in p2['header']
    assert p3['parse'] == 1
    ok.append('playerContent: 主线 parse=%d(带头) / 原站 parse=%d(无Cookie) / 解析 parse=%d'
              % (p1['parse'], p2['parse'], p3['parse']))

    # 5) v2 新增：镜像自动切换
    ss = Session()
    first = ss.host
    ss._rotate()
    assert ss.host != first and ss.host.startswith('https://')
    for _ in range(10):
        ss._rotate()
    assert ss.host.startswith('https://') and len(ss.hosts) == 5
    ok.append('镜像切换: %s -> %s（池 %d 个域名循环，DNS 挂自动换）'
              % (first.split('//')[-1], ss.host.split('//')[-1], len(ss.hosts)))

    # 6) v2 新增：gzip 解压
    raw = gzip.compress('{"code":1}'.encode('utf-8'))
    assert Session._decompress(raw, 'gzip') == b'{"code":1}'
    ok.append('gzip 解压: 压缩包 %dB -> 明文 %dB（首页 230KB 约降到 40KB）'
              % (len(raw), len(b'{"code":1}')))

    # 7) v2 新增：请求头带 Accept-Encoding（提速关键）
    h = Session._base_headers(ss)
    assert 'gzip' in h.get('Accept-Encoding', '')
    ok.append('请求头: Accept-Encoding=%s ｜ UA=%s…' % (h['Accept-Encoding'], h['User-Agent'][:28]))

    # 8) 首页失败诊断卡
    real_home = sp.sess.home_html
    sp.sess.home_html = lambda *a, **k: ""
    dg = sp.homeContent()
    assert dg['list'][0]['vod_name'].startswith('[诊断]')
    ok.append('首页失败兜底: %s ｜ %s' % (dg['list'][0]['vod_name'], dg['list'][0]['vod_content'][:70]))
    sp.sess.home_html = real_home

    print('====== gaze2.py 自测（真实页面样本 mock 网络层） ======')
    for i, line in enumerate(ok, 1):
        print(' ✅ %d) %s' % (i, line))
    print(' ====== 全部通过 ======  requests 可用: %s' % gaze.HAS_REQUESTS)


if __name__ == '__main__':
    main()
