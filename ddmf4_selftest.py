# -*- coding: utf-8 -*-
"""ddmf.py 业务自测（五接口真机连跑 + 契约断言）"""
import sys, json, importlib.util

CHANNEL = 'https://jsnzkpg4.pages.dev/'
PASS, FAIL = [], []

def ck(name, cond, extra=''):
    if cond:
        PASS.append(name); print('  OK  ' + name + ((' | ' + str(extra)[:120]) if extra else ''))
    else:
        FAIL.append(name); print('  NG  ' + name + ((' | ' + str(extra)[:200]) if extra else ''))

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else CHANNEL
    spec = importlib.util.spec_from_file_location('ddmf_mod', '/workspace/ddmf4.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    S = m.Spider()
    # v2：默认直连 + 自动切公共代理通道（模拟直连被挡的真实场景）
    S.init({})

    print('\n[1] homeContent 分类')
    h = S.homeContent(True); cls = h.get('class') or []
    ck('homeContent 返回 class', isinstance(cls, list) and len(cls) > 0, '共 %d 个分类' % len(cls))
    ck('分类含 type_id/type_name', all('type_id' in c and 'type_name' in c for c in cls))
    ids = [c['type_id'] for c in cls]
    ck('空分类已剔除', '19' not in ids and '33' not in ids and '45' not in ids)

    print('\n[2] homeVideoContent 首页推荐')
    hv = S.homeVideoContent(); L = hv.get('list') or []
    ck('首页有内容', len(L) > 0, '%d 条' % len(L))
    ck('卡片字段齐全', all(all(k in v for k in ('vod_id','vod_name','vod_pic')) for v in L))
    ck('片名无 $ #', all('$' not in v['vod_name'] and '#' not in v['vod_name'] for v in L))
    ck('首页海报100%非空', bool(L) and all(v.get('vod_pic') for v in L),
       '%d/%d' % (sum(1 for v in L if v.get('vod_pic')), len(L)))
    ck('海报是http URL', all(str(v.get('vod_pic') or '').startswith('http') for v in L),
       (L[0].get('vod_pic') or '')[:60] if L else '')

    print('\n[3] categoryContent 分类列表 + 分页')
    c1 = S.categoryContent('1', 1, False, {}); cl = c1.get('list') or []
    ck('分类1 有内容', len(cl) > 0, '%d 条' % len(cl))
    ck('分类返回契约字段', all(k in c1 for k in ('list','page','pagecount','limit','total')))
    ck('page 为 int', isinstance(c1.get('page'), int), repr(c1.get('page')))
    ck('pagecount >= 1', int(c1.get('pagecount') or 0) >= 1, c1.get('pagecount'))
    ck('total 有效', int(c1.get('total') or 0) > 0, c1.get('total'))
    ck('分类海报100%非空', bool(cl) and all(x.get('vod_pic') for x in cl),
       '%d/%d' % (sum(1 for x in cl if x.get('vod_pic')), len(cl)))
    c2 = S.categoryContent('1', 2, False, {})
    ck('第2页有内容', len(c2.get('list') or []) > 0, '%d 条' % len(c2.get('list') or []))
    ck('第2页内容不同于第1页', bool(cl) and bool(c2.get('list')) and cl[0]['vod_id'] != (c2.get('list') or [{}])[0].get('vod_id'))

    print('\n[4] detailContent 详情 + 线路契约')
    vid = cl[0]['vod_id'] if cl else (L[0]['vod_id'] if L else '')
    d = S.detailContent([vid]); dl = d.get('list') or []
    ck('详情有内容', len(dl) > 0, vid)
    v = None
    if dl:
        v = dl[0]
        ck('详情字段齐全', all(k in v for k in ('vod_id','vod_name','vod_pic','vod_play_from','vod_play_url')))
        pfrom = v['vod_play_from'].split('$$$'); purl = v['vod_play_url'].split('$$$')
        ck('线路名数 == 播放段数', len(pfrom) == len(purl), '%d vs %d' % (len(pfrom), len(purl)))
        ck('线路名非空且不重复', all(x.strip() for x in pfrom) and len(set(pfrom)) == len(pfrom), pfrom)
        ck('每段都有集名$地址', all('$' in s for s in purl))
        ck('每段都有 m3u8', all('.m3u8' in s for s in purl))
        ck('集名不重复', all(len(set([e.split('$')[0] for e in s.split('#') if e])) == len([e for e in s.split('#') if e]) for s in purl))

    print('\n[5] playerContent 播放')
    if v:
        seg = v['vod_play_url'].split('$$$')[0]; ep = seg.split('#')[0]; url = ep.split('$')[-1]
        p = S.playerContent('江南云', url, None)
        ck('playerContent 返回 dict', isinstance(p, dict))
        ck('parse=0', p.get('parse') == 0, p.get('parse'))
        ck('url 与直链一致', p.get('url') == url, p.get('url'))
        ck('header 是 dict', isinstance(p.get('header'), dict))

    print('\n[6] searchContent 搜索')
    s1 = S.searchContent('海贼王', False, '1'); sl = s1.get('list') or []
    ck('搜索有结果', len(sl) > 0, '%d 条' % len(sl))
    ck('搜索契约字段', all(k in s1 for k in ('list','page','pagecount','limit','total')))
    if sl: ck('搜索结果有片名', bool(sl[0].get('vod_name')), sl[0].get('vod_name'))
    ck('搜索海报100%非空', bool(sl) and all(x.get('vod_pic') for x in sl),
       '%d/%d' % (sum(1 for x in sl if x.get('vod_pic')), len(sl)))

    print('\n[7] localProxy 契约')
    r = S.localProxy()
    ck('localProxy 返回 list', isinstance(r, (list, tuple)))
    ck('localProxy >= 3 位', len(r) >= 3, len(r))
    ck('localProxy header 是 dict', len(r) < 4 or isinstance(r[3], dict))

    print('\n[8] 加载层契约')
    ck('getDependence == []', S.getDependence() == [])
    ck('getName 非空', bool(S.getName()), S.getName())
    ck('isVideoFormat m3u8', S.isVideoFormat('http://a/b.m3u8') is True)
    ck('isVideoFormat 非视频', S.isVideoFormat('http://a/b.html') is False)

    print('\n' + '='*60)
    print('通过 %d / 失败 %d' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL: print('  FAIL: ' + f)
        sys.exit(1)
    print('ALL PASS')

if __name__ == '__main__':
    main()
