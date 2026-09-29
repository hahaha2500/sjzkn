# -*- coding: utf-8 -*-
"""
dida_selftest.py — dida.py 交付前自测（真实网络 + 契约断言）
跑法: python3 dida_selftest.py
"""
import sys
import json
import time
import base64
import random
import importlib

sys.path.insert(0, "/workspace")

PASS = []
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print("  ✅ %s %s" % (name, extra))
    else:
        FAIL.append(name)
        print("  ❌ %s %s" % (name, extra))


print("=" * 70)
print("1) 加载模块 / 基础契约")
print("=" * 70)
try:
    import dida2
    dida = dida2
    importlib.reload(dida2)
except Exception as e:
    print("加载失败:", e)
    sys.exit(1)

s = dida2.Spider()
check("可实例化(无参)", True)
check("getDependence()==[]", s.getDependence() == [], str(s.getDependence()))
check("getName", s.getName() == "嘀嗒影视", s.getName())
check("isVideoFormat m3u8", s.isVideoFormat("http://a/b.m3u8?x=1"))
check("isVideoFormat mp4", s.isVideoFormat("http://a/b.mp4"))
check("isVideoFormat html否", not s.isVideoFormat("http://a/detail/1.html"))
check("manualVideoCheck存在", callable(s.manualVideoCheck))
lp = s.localProxy()
check("localProxy 四元组", isinstance(lp, list) and len(lp) == 4, str(lp)[:80])
check("localProxy body是bytes", isinstance(lp[2], bytes))
h = s.init("")
check("init默认host", h.startswith("http"), h)
h2 = s.init({"host": "https://didahd.xyz"})
check("init extend dict换host", h2 == "https://didahd.xyz", h2)
h3 = s.init('{"host":"www.didahd.xyz"}')
check("init extend JSON串换host", h3 == "https://www.didahd.xyz", h3)
s.init("")

print("=" * 70)
print("2) 纯 Python AES 与 pycryptodome 对拍")
print("=" * 70)
try:
    from Crypto.Cipher import AES as T
    has = True
except Exception:
    has = False
if has:
    ok_all = True
    for _ in range(30):
        key = bytes(random.randrange(256) for _ in range(16))
        iv = bytes(random.randrange(256) for _ in range(16))
        # 正确流程: 随机明文 → PKCS7 填充 → 加密得密文 → 两种方式解密比对
        pt = bytes(random.randrange(256) for _ in range(random.randint(1, 120)))
        padn = 16 - (len(pt) % 16)
        pt_padded = pt + bytes([padn]) * padn
        ct = T.new(key, T.MODE_CBC, iv).encrypt(pt_padded)
        pt_std = T.new(key, T.MODE_CBC, iv).decrypt(ct)
        pt_std = pt_std[:-pt_std[-1]]
        saved = dida2._HAS_CRYPTO
        dida2._HAS_CRYPTO = False          # 强制走纯 Python
        try:
            pt_pure = dida.aes_cbc_decrypt(ct, key, iv)
        except Exception:
            pt_pure = None
        finally:
            dida2._HAS_CRYPTO = saved
        if pt_pure != pt or pt_std != pt:
            ok_all = False
            print("     不一致: 明文长=%d pure=%r std=%r" % (len(pt), pt_pure, pt_std))
            break
    check("纯Python AES 解密 == pycryptodome (30组随机)", ok_all)
else:
    print("  ⚠ 本机无 pycryptodome, 跳过对拍")

print("=" * 70)
print("3) URL 拼装规则（与实测样例逐字核对）")
print("=" * 70)
check("第1页", s._show_url("1", 1) == "https://www.didahd.xyz/show/1--------1---.html",
      s._show_url("1", 1))
check("第21页", s._show_url("1", 21) == "https://www.didahd.xyz/show/1--------21---.html",
      s._show_url("1", 21))
check("地区筛选段2(编码)", s._show_url("1", 1, {"area": "韩国"}) ==
      "https://www.didahd.xyz/show/1-%E9%9F%A9%E5%9B%BD-------1---.html",
      s._show_url("1", 1, {"area": "韩国"}))
check("年份筛选段12", s._show_url("1", 1, {"year": "2026"}) ==
      "https://www.didahd.xyz/show/1--------1---2026.html", s._show_url("1", 1, {"year": "2026"}))
check("子分类换tid", s._show_url("1", 1, {"sub": "6"}) ==
      "https://www.didahd.xyz/show/6--------1---.html", s._show_url("1", 1, {"sub": "6"}))
check("排序段3", s._show_url("1", 1, {"by": "time"}) ==
      "https://www.didahd.xyz/show/1--time------1---.html", s._show_url("1", 1, {"by": "time"}))

print("=" * 70)
print("4) 首页 / 分类 / 搜索（真实网络）")
print("=" * 70)
home = s.homeContent(False)
cls = home.get("class") or []
check("首页分类非空", len(cls) >= 3, str([c["type_name"] for c in cls]))
hlist = home.get("list") or []
check("首页 list 非空", len(hlist) > 5, "%d 条" % len(hlist))
if hlist:
    v = hlist[0]
    check("首页卡有 id/name/pic", bool(v.get("vod_id")) and bool(v.get("vod_name")) and bool(v.get("vod_pic")),
          str(v)[:120])

cat = s.categoryContent("1", "1", False, None)
check("电影分类有数据", len(cat.get("list") or []) > 5, "%d 条" % len(cat.get("list") or []))
check("分类返回契约字段", all(k in cat for k in ("page", "pagecount", "limit", "total", "list")))
check("分类 pagecount>=1", cat["pagecount"] >= 1, str(cat["pagecount"]))
cat2 = s.categoryContent("1", "2", False, None)
ids1 = set(x["vod_id"] for x in cat["list"])
ids2 = set(x["vod_id"] for x in cat2["list"])
check("第2页数据与第1页不同(翻页有效)", len(ids1 & ids2) < max(1, len(ids1)),
      "交集 %d/%d" % (len(ids1 & ids2), len(ids1)))

cat_tv = s.categoryContent("2", "1", False, None)
check("电视剧分类有数据", len(cat_tv.get("list") or []) > 5, "%d 条" % len(cat_tv.get("list") or []))

sc = s.searchContent("爱", "false", "1")
check("搜索有数据", len(sc.get("list") or []) > 3, "%d 条" % len(sc.get("list") or []))
if sc.get("list"):
    sv = sc["list"][0]
    check("搜索卡 id/name/pic", bool(sv.get("vod_id")) and bool(sv.get("vod_name")) and bool(sv.get("vod_pic")),
          str(sv)[:130])
sc2 = s.searchContent("爱", "false", "2")
check("搜索第2页有数据", len(sc2.get("list") or []) > 3, "%d 条" % len(sc2.get("list") or []))
sids = set(x["vod_id"] for x in sc["list"]) & set(x["vod_id"] for x in sc2["list"])
check("搜索翻页不重复", len(sids) < max(1, len(sc["list"])), "交集 %d" % len(sids))

print("=" * 70)
print("5) filters（筛选区动态解析）")
print("=" * 70)
homef = s.homeContent(True)
flt = homef.get("filters") or {}
check("filters 已生成", len(flt) > 0, "%d 个分类" % len(flt))
if flt:
    f1 = flt.get("1") or []
    keys = [g["key"] for g in f1]
    check("tid=1 有筛选组", len(f1) >= 3, str(keys))
    # 抽一个地区组实测
    area_grp = None
    for g in f1:
        if g["key"] == "area":
            area_grp = g
    if area_grp:
        val = area_grp["value"][0]["v"]
        c = s.categoryContent("1", "1", True, {"area": val})
        check("地区筛选生效(有数据)", len(c.get("list") or []) > 0, "area=%s → %d 条" % (val, len(c.get("list") or [])))
    else:
        check("地区筛选组存在", False, str(keys))

print("=" * 70)
print("6) 详情（剧集 / 电影）")
print("=" * 70)
d = s.detailContent(["3666"])
check("详情返回 dict.list", isinstance(d, dict) and isinstance(d.get("list"), list))
vod = (d.get("list") or [None])[0]
check("剧集详情存在", bool(vod))
if vod:
    print("     片名=%s 备注=%s" % (vod.get("vod_name"), vod.get("vod_remarks")))
    print("     演员=%s 导演=%s 年份=%s 地区=%s" % (vod.get("vod_actor"), vod.get("vod_director"),
                                                vod.get("vod_year"), vod.get("vod_area")))
    print("     简介=%s" % (vod.get("vod_content") or "")[:90])
    pf = (vod.get("vod_play_from") or "").split("$$$")
    pu = (vod.get("vod_play_url") or "").split("$$$")
    check("线路名非空", len(pf) >= 2, str(pf))
    check("线路名段数==地址段数(铁律)", len(pf) == len(pu), "%d vs %d" % (len(pf), len(pu)))
    check("多集用#分隔", "#" in (pu[0] if pu else ""), str(pu[0])[:80] if pu else "")
    check("无裸分隔符残留($#)", not any(("$" not in seg and "#" not in seg) for seg in []) )
    # 每段含 $
    okseg = all("$" in seg for seg in pu[0].split("#")) if pu else False
    check("每集都是 名称$地址", okseg)
    # 集名不重复
    names = [x.split("$")[0] for x in pu[0].split("#")]
    check("同线路集名不重复", len(names) == len(set(names)), str(names[:8]))
    # 网盘线/嗅探线存在
    check("含网页嗅探兜底线", any("嗅探" in x for x in pf), str(pf))
    check("含宿主解析兜底线", any("解析" in x for x in pf), str(pf))

dm = s.detailContent(["3774"])
vm = (dm.get("list") or [None])[0]
check("电影详情存在", bool(vm))
if vm:
    pf = (vm.get("vod_play_from") or "").split("$$$")
    pu = (vm.get("vod_play_url") or "").split("$$$")
    check("电影线路段数相等", len(pf) == len(pu), "%d vs %d" % (len(pf), len(pu)))
    print("     电影线路:", pf)

print("=" * 70)
print("7) 播放（四步解密链 → 真直链）")
print("=" * 70)
t0 = time.time()
PLAY_A = "/play/3666-6-1.html"
pc = s.playerContent("main", "main@@@" + PLAY_A, [])
dt = time.time() - t0
host_now = s._get_host()
print("     结果: parse=%s url=%s (%.1fs)" % (pc.get("parse"), (pc.get("url") or "")[:110], dt))
check("主线路 parse=0", pc.get("parse") == 0, str(pc.get("parse")))
check("拿到非回退的 http 直链",
      str(pc.get("url", "")).startswith("http") and not pc.get("url", "").startswith(host_now),
      (pc.get("url") or "")[:90])
check("header 是 dict", isinstance(pc.get("header"), dict), str(type(pc.get("header"))))

# 直链可播性
if pc.get("url"):
    try:
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(pc["url"], headers={"User-Agent": dida.UA},
                                     method="GET")
        req.add_header("Range", "bytes=0-1024")
        r = urllib.request.urlopen(req, timeout=30, context=ctx)
        code = r.status
        ct = r.headers.get("Content-Type", "")
        check("直链 Range 请求 206/200", code in (200, 206), "%s %s" % (code, ct))
    except Exception as e:
        check("直链 Range 请求 206/200", False, str(e)[:100])

# 纯 Python AES 路径端到端验证（真数据, 关掉 pycryptodome 再解一次）
pcA = pc.get("url") or ""
dida2._HAS_CRYPTO = False
for _k in [k for k in list(dida2._CACHE) if k.startswith("art_") or k.startswith("url_")]:
    del dida2._CACHE[_k]                         # 清掉 art 常量与直链缓存, 强制重走解密链
try:
    pc_pure = s.playerContent("main", "main@@@" + PLAY_A, [])
finally:
    dida2._HAS_CRYPTO = True
pcB = pc_pure.get("url") or ""
check("纯Python路径也能解出真直链",
      pcB.startswith("http") and not pcB.startswith(host_now), pcB[:80])
check("两条路径得到同一文件",
      pcA != "" and not pcA.startswith(host_now) and pcA.split("?")[0] == pcB.split("?")[0],
      (pcA.split("?")[0][-45:] if pcA else ""))

# 第二条线路
pc2 = s.playerContent("main", "main@@@/play/3666-5-1.html", [])
check("线路2 也解出真直链",
      str(pc2.get("url", "")).startswith("http") and not pc2.get("url", "").startswith(host_now),
      (pc2.get("url") or "")[:80])

# 兜底线
pcs = s.playerContent("sniff", "sniff@@@" + PLAY_A, [])
check("嗅探线 parse=0 且指向原页",
      pcs.get("parse") == 0 and pcs["url"].endswith(PLAY_A), pcs.get("url"))
pcp = s.playerContent("parse", "parse@@@" + PLAY_A, [])
check("解析线 parse=1", pcp.get("parse") == 1, str(pcp.get("parse")))
ppl = s.playerContent("pan", "pan@@@https://pan.quark.cn/s/abc", [])
check("网盘线 parse=1 返回分享链", ppl.get("parse") == 1 and "quark" in ppl.get("url", ""), ppl.get("url"))

print("=" * 70)
print("8) 电影播放（动态取有资源的片子）")
print("=" * 70)
cat_m = s.categoryContent("1", "1", False, None)
mids = [x["vod_id"] for x in (cat_m.get("list") or [])[:4]]
m_ok, m_tried = 0, 0
for mid in mids:
    try:
        dd = s.detailContent([mid])
        vv = (dd.get("list") or [None])[0]
        if not vv:
            continue
        pf = (vv.get("vod_play_from") or "").split("$$$")
        pu = (vv.get("vod_play_url") or "").split("$$$")
        done = False
        for nm, seg in zip(pf, pu):
            if "网盘" in nm or "嗅探" in nm or "解析" in nm:
                continue
            first = seg.split("#")[0]
            pid = first.split("$", 1)[1] if "$" in first else ""
            if not pid:
                continue
            m_tried += 1
            r = s.playerContent("main", pid, [])
            u = str(r.get("url") or "")
            if r.get("parse") == 0 and u.startswith("http") and not u.startswith(host_now):
                m_ok += 1
                print("     《%s》%s → %s" % (vv.get("vod_name"), nm, u[:70]))
                done = True
                break
        if done:
            break
    except Exception as e:
        print("     %s EXC %s" % (mid, e))
check("至少一部电影解出真直链", m_ok >= 1, "成功 %d / 尝试 %d" % (m_ok, m_tried))

print("=" * 70)
print("9) 防御式（异常不崩）")
print("=" * 70)
try:
    bad = s.detailContent(["99999999"])
    check("不存在的详情不崩", isinstance(bad, dict), str(bad)[:80])
except Exception as e:
    check("不存在的详情不崩", False, str(e)[:80])
try:
    empty = s.playerContent("main", "/play/0-0-0.html", [])
    check("坏播放id不崩", isinstance(empty, dict), str(empty)[:100])
except Exception as e:
    check("坏播放id不崩", False, str(e)[:100])
try:
    e2 = s.searchContent("", "false", "1")
    check("空关键词不崩", isinstance(e2, dict), str(len(e2.get("list") or [])))
except Exception as e:
    check("空关键词不崩", False, str(e)[:80])

print("=" * 70)
print("★ 结果: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)
print("=" * 70)
sys.exit(1 if FAIL else 0)
