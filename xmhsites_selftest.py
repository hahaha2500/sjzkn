import sys, os, json, time, random, urllib.request, importlib.util
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
s = importlib.util.spec_from_file_location("sg", os.path.join(H, "sibgroup.py"))
m = importlib.util.module_from_spec(s); sys.modules["sg"] = m; s.loader.exec_module(m)
NG = []
def chk(n, c, x=""):
    print(("  OK  " if c else "  NG  ") + n + ("  " + str(x)[:120] if x != "" else ""))
    if not c: NG.append(n)

sp = m.Spider(); sp.init("")
print("=== 1 加载层 ===")
chk("getDependence 空", sp.getDependence() == [])
chk("isVideoFormat", sp.isVideoFormat("a.m3u8") and not sp.isVideoFormat("a.html"))
hc = sp.homeContent(True)
chk("分类=站数>=100", len(hc.get("class", [])) >= 100, "站数=%d" % len(hc["class"]))
chk("每站字段齐", all("type_id" in c and "type_name" in c for c in hc["class"]))

print("\n=== 2 分类（抽6站） ===")
random.seed(3)
ids = random.sample(range(len(m._DEF)), 6)
for i in ids:
    t0 = time.time(); c = sp.categoryContent(str(i), "1", False, {}); dt = time.time() - t0
    chk("站%d 出内容" % i, len(c["list"]) > 0, "%s %d条 %.1fs pc=%s" % (m._DEF[i].split("|")[0], len(c["list"]), dt, c["pagecount"]))
c = sp.categoryContent(str(ids[0]), "1", False, {})
chk("分类契约", all(k in c for k in ("list","page","pagecount","limit","total")))
chk("page 是 int", isinstance(c["page"], int))
chk("卡片字段齐", all(all(k in it for k in ("vod_id","vod_name","vod_pic","vod_remarks")) for it in c["list"]))
chk("vod_id 无 $ #", all("$" not in it["vod_id"] and "#" not in it["vod_id"] for it in c["list"]))
chk("style 生效", c["list"][0]["style"].get("span") == 6, c["list"][0]["style"])
c2 = sp.categoryContent(str(ids[0]), "2", False, {})
i1 = set(x["vod_id"] for x in c["list"]); i2 = set(x["vod_id"] for x in c2["list"])
chk("第2页不同", len(i1 & i2) < len(i1) * 0.5, "重合%d/%d" % (len(i1 & i2), len(i1)))

print("\n=== 3 首页混合 ===")
t0 = time.time(); hv = sp.homeVideoContent(); dt = time.time() - t0
chk("首页出卡", len(hv.get("list", [])) > 0, "%d条 %.1fs" % (len(hv.get("list",[])), dt))

print("\n=== 4 详情 ===")
it = c["list"][0]
d = sp.detailContent([it["vod_id"]])["list"][0]
chk("详情契约", all(k in d for k in ("vod_id","vod_name","vod_play_from","vod_play_url")))
froms = d["vod_play_from"].split("$$$"); segs = d["vod_play_url"].split("$$$")
chk("线路名==地址段", len(froms) == len(segs), "%d vs %d" % (len(froms), len(segs)))
chk("每集 名$地址", all(p.count("$") == 1 for sg in segs for p in sg.split("#")))

print("\n=== 5 播放（真取流+真拉） ===")
ok = 0
for k in range(min(5, len(c["list"]))):
    dd = sp.detailContent([c["list"][k]["vod_id"]])["list"][0]
    g = dd["vod_play_url"].split("$$$"); f = dd["vod_play_from"].split("$$$")
    pl = sp.playerContent(f[0], g[0].split("$")[1], [])
    u = pl.get("url", "")
    good = ".m3u8" in u and pl.get("parse") == 0
    if good:
        try:
            body = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": m.UA_POOL[0]}), timeout=20).read().decode("utf-8","replace")
            good = "#EXTM3U" in body
        except Exception:
            good = False
    ok += good
    print("       %s %-22s parse=%s %s" % ("OK" if good else "NG", c["list"][k]["vod_name"][:22], pl.get("parse"), u[:56]))
chk("取流成功>=3/5", ok >= 3, "%d/5" % ok)

print("\n=== 6 搜索 ===")
t0 = time.time(); se = sp.searchContent("人妻", False, "1"); dt = time.time() - t0
chk("搜索契约", all(k in se for k in ("list","page","pagecount","limit","total")))
chk("搜索有结果", len(se.get("list", [])) > 0, "%d条 %.1fs" % (len(se.get("list",[])), dt))

print("\n=== 7 边界 ===")
chk("越界 tid 不崩", sp.categoryContent("99999","1",False,{})["list"] == [])
chk("空页刹车", sp.categoryContent(str(ids[0]),"99999",False,{}).get("pagecount",0) <= 99999)
chk("localProxy 四元组", len(sp.localProxy("x")) == 4)
a = m.Spider(); a.init(json.dumps({"span":4,"ratio":0.33}))
chk("extend 生效", a.categoryContent(str(ids[0]),"1",False,{})["list"][0]["style"] == {"type":0,"span":4,"ratio":0.33})
print("\n==== %s ====" % ("ALL PASS" if not NG else "NG: "+", ".join(sorted(set(NG)))))
