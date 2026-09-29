# -*- coding: utf-8 -*-
"""开心影院 kxyy.py 自测 —— 六接口真机连跑（本环境可直连站点）"""
import sys, time, json, importlib.util

spec = importlib.util.spec_from_file_location("kxyy", "/workspace/kxyy.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
S = mod.Spider()
S.init("")
print("=" * 66)

def show(name, ok, detail=""):
    print(("  [OK] " if ok else "  [NG] ") + name + ("  " + detail if detail else ""))
    return ok

allok = True

# 0) 加载层契约
allok &= show("getDependence() == []", S.getDependence() == [], repr(S.getDependence()))
allok &= show("getName()", S.getName() == "开心影院", S.getName())
allok &= show("isVideoFormat", S.isVideoFormat("x/index.m3u8") and not S.isVideoFormat("a.html"))
allok &= show("manualVideoCheck", S.manualVideoCheck() is False)

# 1) 首页
t = time.time()
home = S.homeContent(filter=True)
lst = home.get("list", [])
allok &= show("homeContent", isinstance(home.get("class"), list) and len(lst) > 0,
              "%d分类 %d片 %.1fs" % (len(home.get("class", [])), len(lst), time.time() - t))
print("      分类:", [(c["type_id"], c["type_name"]) for c in home.get("class", [])])
print("      首片:", lst[0] if lst else None)
allok &= show("filters 已返回", "filters" in home, str(list(home.get("filters", {}).keys())))

# 2) homeVideoContent
hv = S.homeVideoContent()
allok &= show("homeVideoContent", len(hv.get("list", [])) > 0, "%d 条" % len(hv.get("list", [])))

# 3) 分类
t = time.time()
cat = S.categoryContent("1", "1")
l = cat.get("list", [])
allok &= show("categoryContent 电影", len(l) > 0,
              "%d卡 page=%s pc=%s total=%s %.1fs" % (len(l), cat.get("page"), cat.get("pagecount"),
                                                     cat.get("total"), time.time() - t))
print("      首卡:", l[0] if l else None)
allok &= show("契约字段齐全", all(k in cat for k in ("page", "pagecount", "limit", "total", "list")))

# 分页（位[8] 页码）
c2 = S.categoryContent("1", "2")
n2 = [x["vod_id"] for x in c2.get("list", [])]
n1 = [x["vod_id"] for x in cat.get("list", [])]
allok &= show("第2页有数据且与第1页不同", len(n2) > 0 and set(n1) != set(n2),
              "%d卡, 重叠=%d" % (len(n2), len(set(n1) & set(n2))))

# 其它分类
for tid, nm in (("2", "电视剧"), ("4", "动漫"), ("26", "短剧")):
    c = S.categoryContent(tid, "1")
    allok &= show("分类 %s %s" % (tid, nm), len(c.get("list", [])) > 0, "%d卡" % len(c.get("list", [])))

# 空页刹车
cb = S.categoryContent("1", "99999")
allok &= show("空页刹车 pagecount<=pg", int(cb.get("pagecount", 0)) <= 99999,
              "pc=%s list=%d" % (cb.get("pagecount"), len(cb.get("list", []))))

# 4) 搜索（含验证码 OCR）
t = time.time()
s = S.searchContent("流浪地球", False, "1")
sl = s.get("list", [])
allok &= show("searchContent", len(sl) > 0, "%d条 %.1fs" % (len(sl), time.time() - t))
for it in sl[:3]:
    print("      ", it.get("vod_id"), it.get("vod_name"), "|", it.get("vod_remarks"))
s2 = S.searchContent("兰香如故", False, "1")
allok &= show("searchContent 第二词", len(s2.get("list", [])) > 0, "%d条" % len(s2.get("list", [])))

# 5) 详情（4线路）
t = time.time()
d = S.detailContent(["134597"])
dl = d.get("list", [])
allok &= show("detailContent", len(dl) == 1, "%.1fs" % (time.time() - t))
if dl:
    it = dl[0]
    print("      名称:", it.get("vod_name"))
    print("      海报:", (it.get("vod_pic") or "")[:70])
    print("      备注:", it.get("vod_remarks"), "| 评分:", it.get("vod_score"))
    print("      导演:", it.get("vod_director"), "| 主演:", (it.get("vod_actor") or "")[:50])
    print("      年份:", it.get("vod_year"), "| 地区:", it.get("vod_area"))
    print("      简介:", (it.get("vod_content") or "")[:80])
    froms = (it.get("vod_play_from") or "").split("$$$")
    urls = (it.get("vod_play_url") or "").split("$$$")
    print("      线路数:", len(froms), "| 段数相等:", len(froms) == len(urls))
    for f, u in zip(froms, urls):
        eps = u.split("#")
        print("        -", f, "集数=%d 首集=%s" % (len(eps), eps[0] if eps else ""))
    allok &= show("线路>=4 且段数相等", len(froms) >= 4 and len(froms) == len(urls))
    allok &= show("名称/海报非空", bool(it.get("vod_name")) and bool(it.get("vod_pic")))
    # 分隔符铁律
    allok &= show("每集含 $ 分隔", all("$" in seg for seg in urls[0].split("#")))

# 6) 播放（三条明文线路 + 加密线路降级）
for sid, expect in (("1", "明文"), ("3", "明文"), ("4", "明文"), ("2", "加密")):
    t = time.time()
    pid = "main@@134597@@%s@@1" % sid
    p = S.playerContent("开心·LZ源", pid, [])
    u = p.get("url", "")
    ok = p.get("parse") == 0 and str(u).startswith("http") and (".m3u8" in u or ".mp4" in u)
    if expect == "加密":
        ok = p.get("parse") in (0, 1) and str(u).startswith("http")
        print("  [%s] sid=%s(%s) -> parse=%s %s  %.1fs" % (
            "OK" if ok else "NG", sid, expect, p.get("parse"), u[:80], time.time() - t))
    else:
        print("  [%s] sid=%s(%s) -> parse=%s %s  %.1fs" % (
            "OK" if ok else "NG", sid, expect, p.get("parse"), u[:80], time.time() - t))
    allok &= show("播放 sid=%s" % sid, ok)
    allok &= show("  header 是 dict", isinstance(p.get("header"), dict))

# sniff 线
ps = S.playerContent("开心·LZ源·嗅探", "sniff@@134597@@1@@1", [])
allok &= show("sniff 线回原页 parse0", ps.get("parse") == 0 and "/vodplay/" in ps.get("url", ""),
              ps.get("url", ""))

# 7) 真实 m3u8 探活
import subprocess
url = S._resolve("134597", "1", "1")
if url:
    r = subprocess.run(["curl", "-s", "-m", "20", "-A", mod.UA, "-e", "https://www.kxyy.app/",
                        "-w", "\n%{http_code}", url], capture_output=True, text=True)
    code = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
    allok &= show("LZ源 m3u8 真实探活 200", code == "200", "HTTP=%s url=%s" % (code, url[:70]))
    allok &= show("  含 EXT-X-STREAM-INF", "EXT-X-STREAM-INF" in r.stdout)

# 8) localProxy 四元组
lp = S.localProxy("null")
allok &= show("localProxy 四元组", isinstance(lp, list) and len(lp) == 4, str(lp[0]) + " " + str(lp[1]))
lp2 = S.localProxy(json.dumps({"url": url}) if url else "null")
allok &= show("localProxy 取流", isinstance(lp2, list) and len(lp2) == 4 and lp2[0] == 200,
              "status=%s len=%s" % (lp2[0], len(lp2[2])))

# 9) 防御：非法参数不崩
for fn, args in (("detailContent", ([],)), ("searchContent", ("", False, "1")),
                 ("categoryContent", ("999999", "abc")), ("playerContent", ("", "", []))):
    try:
        r = getattr(S, fn)(*args)
        ok = isinstance(r, dict)
    except Exception as e:
        ok, r = False, e
    allok &= show("防御 %s%s" % (fn, args), ok, str(r)[:60])

print("=" * 66)
print("RESULT:", "ALL PASS ✅" if allok else "HAS FAILURE ❌")
