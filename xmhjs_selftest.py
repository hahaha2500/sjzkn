import sys, os, importlib.util, time, json, re, urllib.request
H = os.path.dirname(os.path.abspath(__file__))
s = importlib.util.spec_from_file_location("xm", os.path.join(H, "xmhjs.py"))
m = importlib.util.module_from_spec(s); sys.modules["xm"] = m; s.loader.exec_module(m)
sp = m.Spider(); sp.init("")
NG = []
def chk(n, c, x=""):
    print(("  OK  " if c else "  NG  ") + n + ("  " + str(x)[:150] if x != "" else ""))
    if not c: NG.append(n)

print("=== 1 加载层 ===")
chk("getDependence 返回空", sp.getDependence() == [])
chk("isVideoFormat", sp.isVideoFormat("a.m3u8") and not sp.isVideoFormat("a.html"))
h = sp.homeContent(True)
chk("homeContent 契约", isinstance(h.get("class"), list) and len(h["class"]) == 11, "分类数=%d" % len(h.get("class", [])))
chk("分类 type_id 全数字", all(c["type_id"].isdigit() for c in h["class"]))

print("\n=== 2 首页/分类（真机） ===")
t0 = time.time(); hv = sp.homeVideoContent(); t1 = time.time()
chk("homeVideoContent 出卡", len(hv.get("list", [])) > 0, "%d条 %.1fs" % (len(hv.get("list", [])), t1-t0))
c1 = sp.categoryContent("20", "1", False, {})
chk("分类契约", all(k in c1 for k in ("list", "page", "pagecount", "limit", "total")), 
    "page=%s pc=%s n=%d" % (c1.get("page"), c1.get("pagecount"), len(c1.get("list", []))))
chk("page 是 int", isinstance(c1.get("page"), int) and not isinstance(c1.get("page"), bool))
chk("卡片字段齐", all(all(k in it for k in ("vod_id", "vod_name", "vod_pic", "vod_remarks")) for it in c1["list"]))
chk("卡片有封面", sum(1 for it in c1["list"] if it["vod_pic"].startswith("http")) > len(c1["list"]) * 0.8)
chk("vod_id 无 $ # 破坏分隔符", all("$" not in it["vod_id"] and "#" not in it["vod_id"] for it in c1["list"]))
chk("pagecount 合理(分页抓到)", c1["pagecount"] > 10, "pc=%s" % c1["pagecount"])
c2 = sp.categoryContent("20", "2", False, {})
id1 = set(re.search(r"@@(\d+)@@", it["vod_id"]).group(1) for it in c1["list"])
id2 = set(re.search(r"@@(\d+)@@", it["vod_id"]).group(1) for it in c2["list"])
chk("第2页内容与第1页不同", len(id1 & id2) < len(id1) * 0.3, "重合 %d/%d" % (len(id1 & id2), len(id1)))
c26 = sp.categoryContent("26", "1", False, {})
n1 = c1["list"][0]["vod_name"][:2] if c1["list"] else ""
n2 = c26["list"][0]["vod_name"][:2] if c26["list"] else ""
chk("不同分类内容不同", not (c1["list"] and c26["list"] and c1["list"][0]["vod_id"] == c26["list"][0]["vod_id"]))

print("\n=== 3 搜索 ===")
se = sp.searchContent("FITCH", False, "1")
chk("搜索契约", all(k in se for k in ("list", "page", "pagecount", "limit", "total")), "%d条" % len(se.get("list", [])))
chk("搜索有结果", len(se.get("list", [])) > 0)

print("\n=== 4 详情 ===")
it = c1["list"][0]
d = sp.detailContent([it["vod_id"]])["list"][0]
chk("详情契约", all(k in d for k in ("vod_id", "vod_name", "vod_pic", "vod_play_from", "vod_play_url")))
chk("片名回填正确", d["vod_name"] == it["vod_name"], d["vod_name"][:40])
chk("封面回填正确", d["vod_pic"] == it["vod_pic"])
froms = d["vod_play_from"].split("$$$")
segs = d["vod_play_url"].split("$$$")
chk("线路名数==地址段数", len(froms) == len(segs), "%d vs %d" % (len(froms), len(segs)))
for seg in segs:
    parts = seg.split("#")
    for p in parts:
        chk_ok = p.count("$") == 1
        if not chk_ok: NG.append("集名$地址格式")
chk("每集 名$地址 格式", all(p.count("$") == 1 for seg in segs for p in seg.split("#")))

print("\n=== 5 播放（真机取流 + 真拉校验） ===")
pid = segs[0].split("$")[1]
pl = sp.playerContent(froms[0], pid, [])
chk("player 契约", isinstance(pl.get("parse"), int) and pl.get("url"))
chk("header 是 dict", isinstance(pl.get("header"), dict))
url = pl["url"]
chk("拿到 m3u8 明文", url.endswith(".m3u8") or ".m3u8" in url, url[:80])
# 真拉
ok = False
try:
    req = urllib.request.Request(url, headers={"User-Agent": m._headers()["User-Agent"]})
    body = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    ok = ("#EXTM3U" in body) and ("#EXT-X-STREAM-INF" in body or ".ts" in body or "/hls/" in body)
    print("       流首行:", body.split("\n")[0][:60], "| 长度", len(body))
except Exception as e:
    print("       拉流异常:", type(e).__name__, str(e)[:80])
chk("m3u8 真拉成功且内容合法", ok)
# 嗅探线
pl2 = sp.playerContent(froms[1] if len(froms) > 1 else "嗅探", segs[1].split("$")[1] if len(segs) > 1 else pid, [])
chk("嗅探线返回原页 parse0", pl2.get("parse") == 0 and "/play/id/" in pl2.get("url", ""))

print("\n=== 6 边界 ===")
e = sp.categoryContent("999", "1", False, {})
chk("无效 tid 不崩", isinstance(e.get("list"), list))
chk("空页刹车", sp.categoryContent("20", "9999", False, {}).get("pagecount", 0) <= 9999)
lp = sp.localProxy("x")
chk("localProxy 四元组", isinstance(lp, list) and len(lp) == 4)
chk("getName", isinstance(sp.getName(), str) and sp.getName() != "")
print("\n缓存统计:", m._CSTAT)
print("\n==== %s ====" % ("ALL PASS" if not NG else "NG: " + ", ".join(sorted(set(NG)))))