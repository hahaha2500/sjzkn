# -*- coding: utf-8 -*-
"""剧踪 spider 五接口自测（我这边先跑通再交给 andy）"""
import importlib.util, sys, json

spec = importlib.util.spec_from_file_location("juzong", "/workspace/juzong.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

s = mod.Spider()
s.init("")
ok = 0
fail = 0


def chk(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ✅ %s %s" % (label, extra))
    else:
        fail += 1
        print("  ❌ %s %s" % (label, extra))


print("== 1) 首页 ==")
h = s.homeContent(False)
chk("class 数量", len(h.get("class", [])) >= 4, "共%d个" % len(h.get("class", [])))
chk("首页列表", len(h.get("list", [])) >= 10, "%d条" % len(h.get("list", [])))
if h.get("list"):
    v = h["list"][0]
    chk("卡片字段", v.get("vod_id") and v.get("vod_name") and v.get("vod_pic"), str(v)[:110])

print("== 2) 分类（第2页） ==")
c = s.categoryContent("1", 2, True, {})
chk("有数据", len(c.get("list", [])) >= 5, "%d条" % len(c.get("list", [])))
chk("契约 pagecount", isinstance(c.get("pagecount"), int) and c.get("pagecount") >= 2, "pagecount=%s" % c.get("pagecount"))
if c.get("list"):
    v = c["list"][0]
    chk("卡片字段", v.get("vod_id") and v.get("vod_name"), str(v)[:110])

print("== 2b) 筛选（地区=大陆 + 排序=time） ==")
c2 = s.categoryContent("1", 1, True, {"area": "大陆", "by": "time"})
chk("筛选出数据", len(c2.get("list", [])) >= 5, "%d条" % len(c2.get("list", [])))

print("== 3) 搜索 ==")
sr = s.searchContent("早春", False, "1")
chk("搜索有数据", len(sr.get("list", [])) >= 3, "%d条" % len(sr.get("list", [])))
if sr.get("list"):
    chk("搜索命中早春晴朗", any("早春" in x["vod_name"] for x in sr["list"]),
        " / ".join(x["vod_name"] for x in sr["list"][:4]))

print("== 4) 详情 ==")
vid = sr["list"][0]["vod_id"] if sr.get("list") else "60937"
d = s.detailContent([str(vid)])
lst = d.get("list", [])
chk("详情有数据", len(lst) == 1, "")
if lst:
    v = lst[0]
    print("     片名=%s" % v.get("vod_name"))
    print("     封面=%s" % (v.get("vod_pic") or "")[:90])
    print("     线路=%s" % v.get("vod_play_from"))
    pf = (v.get("vod_play_url") or "").split("$$$")
    print("     线路段数=%d 集段数=%d" % (len((v.get("vod_play_from") or "").split("$$$")), len(pf)))
    chk("线路数>=2", len((v.get("vod_play_from") or "").split("$$$")) >= 2, "")
    chk("线路/集数段数相等", len((v.get("vod_play_from") or "").split("$$$")) == len(pf), "")
    first = pf[0].split("#")[0]
    chk("分隔符 $ 结构", "$" in first, "样本=" + first)
    chk("简介非空", bool(v.get("vod_content")), "%d字" % len(v.get("vod_content") or ""))

    print("== 5) 播放（逐条线路第1集）==")
    names = (v.get("vod_play_from") or "").split("$$$")
    for i, seg in enumerate(pf):
        line = seg.split("#")[0]
        if "$" not in line:
            continue
        ename, pid = line.split("$", 1)
        flag = names[i] if i < len(names) else ""
        try:
            p = s.playerContent(flag, pid, [])
        except Exception as e:
            p = {"parse": -1, "url": "EXC:" + str(e)[:60]}
        u = p.get("url", "")
        head_ok = isinstance(p.get("header"), dict)
        kind = "直链" if mod.Spider.isVideoFormat(s, u) or ".mp4" in u else ("密文解密失败/回原页" if u.endswith("/") else "外链/原页")
        print("     [%s] parse=%s %s -> %s" % (flag[:14], p.get("parse"), kind, (u or "")[:80]))
        chk("  header是dict[%s]" % flag[:10], head_ok)

print("== 6) 加载层契约 ==")
chk("getDependence=[]", s.getDependence() == [], str(s.getDependence()))
chk("getName", s.getName() == "剧踪", s.getName())
chk("isVideoFormat", s.isVideoFormat("https://a.com/x.m3u8") and not s.isVideoFormat("https://a.com/x.html"))
chk("manualVideoCheck", s.manualVideoCheck() is False)
lp = s.localProxy(None)
chk("localProxy四元组", isinstance(lp, list) and len(lp) == 4, str(lp[:2]))
print()
print("===== 自测: 通过 %d / 失败 %d =====" % (ok, fail))
sys.exit(1 if fail else 0)
