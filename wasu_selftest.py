# -*- coding: utf-8 -*-
"""华数TV spider 契约自测（独立入口，spider 本体不含 __main__）"""
import importlib.util, sys, json
spec = importlib.util.spec_from_file_location("wasu", "/workspace/wasu.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
S = m.Spider(); S.init("")
OK = NG = 0
def chk(name, cond, extra=""):
    global OK, NG
    if cond: OK += 1; print("  ✅ %s %s" % (name, extra))
    else:    NG += 1; print("  ❌ %s %s" % (name, extra))

print("== 1 加载层铁律 ==")
chk("getDependence 返回 []", S.getDependence() == [])
chk("getName 非空", bool(S.getName()))
chk("isVideoFormat m3u8", S.isVideoFormat("a/b.m3u8"))
chk("isVideoFormat mp4", S.isVideoFormat("a/b.mp4"))
chk("isVideoFormat 空=False", S.isVideoFormat("") == False)
chk("可无参实例化", isinstance(m.Spider(), m._BaseSpider))
print("== 2 homeContent ==")
h = S.homeContent(True)
chk("class 5个", len(h.get("class", [])) == 5, str([c["type_name"] for c in h["class"]]))
chk("filters 键与 class 对应", set(h.get("filters", {}).keys()) == set(c["type_name"] for c in h["class"]))
chk("有 list", isinstance(h.get("list"), list) and len(h["list"]) > 0, "n=%d" % len(h.get("list", [])))
hv = S.homeVideoContent()
chk("homeVideoContent 有 list", isinstance(hv.get("list"), list) and len(hv["list"]) > 0, "n=%d" % len(hv.get("list", [])))
print("== 3 categoryContent ==")
for c in h["class"]:
    r = S.categoryContent(c["type_id"], 1, False, {})
    bad = [x for x in r["list"] if not x.get("vod_id") or not x.get("vod_name")]
    chk("分类 %s" % c["type_name"], len(r["list"]) > 0 and not bad,
        "n=%d page=%s/%s" % (len(r["list"]), r.get("page"), r.get("pagecount")))
    chk("  契约键齐全", all(k in r for k in ("list","page","pagecount","limit","total")))
r0 = S.categoryContent(h["class"][0]["type_id"], 1); r2 = S.categoryContent(h["class"][2]["type_id"], 1)
chk("不同分类内容不同(缓存键已含query)", [x["vod_name"] for x in r0["list"][:5]] != [x["vod_name"] for x in r2["list"][:5]])
chk("tid 兼容分类名", len(S.categoryContent("电影", 1)["list"]) > 0)
chk("tid 非法不崩", S.categoryContent("不存在的分类", 1).get("page") == 1)
print("== 4 searchContent ==")
sr = S.searchContent("爱情", False, 1)
chk("搜索有结果", len(sr.get("list", [])) > 0, "n=%d page=%s/%s" % (len(sr.get("list",[])), sr.get("page"), sr.get("pagecount")))
chk("搜索契约齐全", all(k in sr for k in ("list","page","pagecount","limit","total")))
if len(sr.get("list", [])) >= 20:
    sr2 = S.searchContent("爱情", False, 2)
    n1 = [x["vod_name"] for x in sr["list"][:3]]; n2 = [x["vod_name"] for x in sr2["list"][:3]]
    chk("搜索第2页内容不同(真分页)", n1 != n2, str(n2[:2]))
chk("空关键词不崩", S.searchContent("", False, 1).get("page") == 1)
print("== 5 detailContent ==")
pid = sr["list"][0]["vod_id"] if sr.get("list") else "d@@108@@178798@@0"
d = S.detailContent([pid])
v = d["list"][0]
chk("详情有片名", bool(v.get("vod_name")), v.get("vod_name"))
chk("详情有海报", bool(v.get("vod_pic")))
chk("有简介", bool(v.get("vod_content")))
L = v["vod_play_from"].split("$$$"); Sg = v["vod_play_url"].split("$$$")
chk("线路名与地址段数相等 ★", len(L) == len(Sg), "%d vs %d" % (len(L), len(Sg)))
eps = Sg[0].split("#")
chk("多集已拆分", len(eps) > 0, "n=%d" % len(eps))
chk("每集都有 $名$址", all(e.count("$") >= 1 for e in eps))
chk("★集名不重复(否则选集塌陷)", len({e.split("$")[0] for e in eps}) == len(eps))
chk("★无裸 $ 破坏分隔符", not any("$" in e.split("$")[0] for e in eps))
chk("标题无 $ 和 #", "$" not in v["vod_name"] and "#" not in v["vod_name"])
chk("详情失败不崩(垃圾id)", isinstance(S.detailContent(["d@@x@@y@@0"])["list"], list))
print("== 6 playerContent ==")
for i, ln in enumerate(L):
    eid = Sg[i].split("#")[0].split("$", 1)[1]
    p = S.playerContent(ln, eid)
    chk("播放[%s] parse" % ln, p.get("parse") in (0, 1))
    chk("  header 是 dict ★", isinstance(p.get("header"), dict), type(p.get("header")).__name__)
    chk("  返回 url", bool(p.get("url")), p.get("url", "")[:70])
    if "嗅探" not in ln:
        chk("  ★已换流带 auth_key(否则必403)", "auth_key=" in p.get("url",""), p.get("url","")[:60])
# 切集
if len(eps) > 3:
    e10 = eps[2].split("$", 1)[1]
    p10 = S.playerContent(L[0], e10)
    chk("切到第3集 url 变化", p10.get("url") != S.playerContent(L[0], eps[0].split("$",1)[1]).get("url"))
p = S.playerContent("华数·原页嗅探", "s@@0@@108@@178798@@0")
chk("嗅探线回原页", "newsServlet" in p.get("url", ""))
chk("垃圾id不崩", isinstance(S.playerContent("x", "垃圾"), dict))
print("== 7 localProxy ==")
lp = S.localProxy({"url": "https://mcsppic.5g.wasu.tv/cms/cms_images/10001/202404/22/c43f1ce6-35b1-429f-8b14-852a0248e751.png"})
chk("四元组", isinstance(lp, list) and len(lp) == 4, "len=%d" % len(lp))
chk("body 是 bytes ★", isinstance(lp[2], bytes), "size=%d" % len(lp[2] or b""))
chk("header 是 dict", isinstance(lp[3], dict))
lp2 = S.localProxy(None)
chk("None 入参不崩", isinstance(lp2, list) and len(lp2) == 4)
lp3 = S.localProxy("不是url")
chk("垃圾入参返404不返200空图", lp3[0] == 404, "code=%s" % lp3[0])
print("== 8 健壮性 ==")
import os
os.environ["WASU"] = "0"
S.init({"host": "https://www.wasu.cn", "name": "华数"})
chk("init dict 生效", S.getName() == "华数")
S.init('{"name":"华数TV2"}')
chk("init JSON串生效", S.getName() == "华数TV2")
S.init("")
chk("init 空串不崩", bool(S.getName()))
print("\n========== 通过 %d / 失败 %d ==========" % (OK, NG))
sys.exit(1 if NG else 0)
