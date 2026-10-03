# -*- coding: utf-8 -*-
"""SpankBang Spider 离线自测（真实页面样本 + mock 网络）"""
import re
src = open('/workspace/spankbang2.py', encoding='utf-8').read()
src = src.replace('from base.spider import Spider as _BaseSpider',
                  'class _BaseSpider(object):\n        pass', 1)
ns = {}
exec(compile(src, 'sb', 'exec'), ns)
Base = ns['Spider']

PAGES = {
    '/trending_videos/': '/workspace/sb_list.html',
    '/most_popular/': '/workspace/sb_list.html',
    '/s/madison+morgan/': '/workspace/sb_search.html',
    '/a59zq/video/': '/workspace/sb_detail.html',
    '/a59zq/video/sl': '/workspace/sb_short.html',
}
class S(Base):
    def _get(self, url, ttl=0):
        p = PAGES.get(url)
        return open(p, encoding='utf-8').read() if p else ''

OK = []; NG = []
def ck(n, c):
    (OK if c else NG).append(n)

sp = S()
v = sp._cards(open('/workspace/sb_list.html', encoding='utf-8').read())
ck('列表64条', len(v) == 64)
ck('全部有图', all(x['vod_pic'].startswith('https://') for x in v))
ck('全部有名', all(len(x['vod_name']) > 2 for x in v))
ck('id唯一', len(set(x['vod_id'] for x in v)) == 64)
ck('pagecount=33', sp._pagecount(open('/workspace/sb_list.html', encoding='utf-8').read(), 1) == 33)
ck('首页返回list', len(sp.homeVideoContent()['list']) == 24)
ck('首页6分类', len(sp.homeContent()['class']) == 6)
c = sp.categoryContent('trending_videos', 1)
ck('分类五字段契约', set(['page', 'pagecount', 'limit', 'total', 'list']) <= set(c))
ck('分类page=1', c['page'] == 1)
ck('分类第2页页码', sp.categoryContent('trending_videos', 2)['page'] == 2)
ck('分类非空页pagecount>pg', c['pagecount'] > c['page'])
d = sp.detailContent(['a59zq'])['list'][0]
ck('详情名非空', bool(d['vod_name']))
ck('详情封面本片图', '17042390' in d['vod_pic'])
ck('详情有简介', len(d['vod_content']) > 30)
ck('详情有备注', bool(d['vod_remarks']))
ck('双线路$$$', d['vod_play_from'].count('$$$') == 1)
ck('线路名数=地址数', len(d['vod_play_from'].split('$$$')) == len(d['vod_play_url'].split('$$$')))
ck('每段有集名', all('$' in seg for seg in d['vod_play_url'].split('$$$')))
ck('集名不重复', d['vod_play_url'].split('$$$')[0].count('正片') == 1)
p = sp.playerContent(d['vod_play_from'].split('$$$')[0], 'a59zq')
ck('直连=1080p mp4', p['parse'] == 0 and 'vdownload' in p['url'] and '1080p' in p['url'])
ck('签名未丢', 'secure=' in p['url'] and '&_tid=' in p['url'])
ck('&amp;已还原', '&amp;' not in p['url'])
ck('header是dict', isinstance(p['header'], dict))
ck('isVideoFormat', sp.isVideoFormat(p['url']))
ck('不误取预览td.mp4', 'td.mp4' not in p['url'])
p2 = sp.playerContent('SpankBang·原页嗅探', 'sniff@@a59zq')
ck('嗅探线回原页', p2['parse'] == 0 and p2['url'].endswith('/a59zq/video/'))
s = sp.searchContent('madison morgan', False, 1)
ck('搜索四字段契约', set(['page', 'pagecount', 'limit', 'total', 'list']) <= set(s))
ck('搜索有结果', len(s['list']) > 0)
ck('搜索pagecount>=1', s['pagecount'] >= 1)
ck('getDependence空', sp.getDependence() == [])
ck('getName', sp.getName() == 'SpankBang')
ck('localProxy非图404', sp.localProxy('http://x') == [404, 'text/plain', b'', {}])
ck('localProxy dict入参', sp.localProxy({})[0] == 404)
# 未调 init 不崩
fresh = ns['Spider']()
ck('未init可用', isinstance(fresh.homeContent(), dict) and fresh._host0() != '')
ck('player空id不崩', fresh.playerContent('直连', '')['url'] == '')
# 断网
class Dead(S):
    def _get(self, url, ttl=0):
        return ''
dk = Dead()
ck('断网首页不崩', dk.homeVideoContent()['list'] == [])
ck('断网分类五字段', dk.categoryContent('x', 1)['list'] == [])
ck('断网详情不崩', bool(dk.detailContent(['a59zq'])['list'][0]['vod_name']))
ck('断网搜索不崩', dk.searchContent('a')['list'] == [])
ck('断网播放不崩', dk.playerContent('直连', 'a59zq')['parse'] == 0)
print('PASS %d  NG %d' % (len(OK), len(NG)))
for n in NG:
    print('  x', n)
