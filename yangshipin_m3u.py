#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央视频直播 -> M3U 直播源生成器
协议: JCE PidTimeShift + bkliveinfo(cKey) 自动切换, 仅标准库。
用法:
  python3 yangshipin_m3u.py                 # 生成全部频道 -> live_yangshipin.txt
  python3 yangshipin_m3u.py --group CCTV1  # 只生成某组
  python3 yangshipin_m3u.py --serve         # 常驻: 每 30 秒自动重签刷新
输出为 TVBox/直播壳可直接导入的 txt(m3u) 格式。
"""
import argparse, importlib.util, os, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))


def load_spider():
    p = os.path.join(HERE, "yangshipin.py")
    if not os.path.exists(p):
        p = os.path.join(HERE, "央视频.py")
    spec = importlib.util.spec_from_file_location("ysp_mod", p)
    m = importlib.util.module_from_spec(spec)
    sys.argv = [sys.argv[0]]
    spec.loader.exec_module(m)
    return m


def groups(m):
    return {
        "4K超清":  lambda s, n: s in m.TRUE_4K_CHANNELS,
        "付费剧场": lambda s, n: s in ("cctvfyjc", "cctvdyjc", "cctvhjjc"),
        "央视频道": lambda s, n: (s.startswith("cctv") and s not in m.TRUE_4K_CHANNELS) or s == "cetv1",
        "卫视频道": lambda s, n: s.endswith("ws"),
        "CGTN":    lambda s, n: s.startswith("cgtn"),
        "其他":    lambda s, n: s == "guoxue",
    }


def _check(m, url, tries=2):
    """实测该 m3u8 是否可取(裸请求), 防止把被墙域名写进订阅; CDN 偶发抖动故重试"""
    last = False
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': m.UA,
                                                       'Referer': 'https://live.cctv.cn/'})
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 200 and b'#EXTM3U' in r.read(2048):
                    return True
            last = False
        except Exception:
            last = False
        time.sleep(0.6)
    return last


def resolve(m, slug, sid, pid, defn, now):
    """★真直播优先走 bkliveinfo(地址常青, 不会过期); JCE 时移窗口短(默认 60s)只做兜底。
       每个候选都实测能取回 #EXTM3U 才采用, 防止把死链/被墙域名写进订阅。"""
    cands = []
    try:
        cands += m.bk_playurls(sid, pid, defn)          # 真直播(首选)
    except Exception:
        pass
    try:
        cands.append(m.jce_timeshift_url(pid, sid, now - 60, now, defn))  # 时移(兜底)
    except Exception:
        pass
    for u in dict.fromkeys(c for c in cands if c):
        if _check(m, u):
            return u
    return cands[0] if cands else ""


def group_of(gs, slug):
    for g, fn in gs.items():
        if fn(slug, ""):
            return g
    return "央视频"


def build(m, group=None):
    lines = ["#EXTM3U"]
    tvg = "https://live.fanmingming.com/tv/"
    out, ok, bad = [], 0, []

    gs = groups(m)
    if group and group not in gs:
        raise SystemExit("未知分组: %s, 可选: %s" % (group, "/".join(gs)))
    items = [(s, n, si, p, d) for s, n, si, p, d in m.CHANNELS
             if not group or gs[group](s, n)]
    now = int(time.time())
    with ThreadPoolExecutor(max_workers=6) as ex:
        urls = list(ex.map(lambda it: resolve(m, it[0], it[2], it[3], it[4], now), items))

    for (slug, name, sid, pid, defn), url in zip(items, urls):
        if not url:
            bad.append(name)
            continue
        ok += 1
        fn = m._logo_file(slug, name) or (slug.upper() + ".png")
        lines.append('#EXTINF:-1 tvg-id="%s" tvg-logo="%s%s" group-title="%s",%s'
                     % (slug, tvg, urllib.parse.quote(fn), group or group_of(gs, slug), name))
        lines.append(url)
    return "\n".join(lines) + "\n", ok, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group")
    ap.add_argument("--out", default=os.path.join(HERE, "live_yangshipin.txt"))
    ap.add_argument("--serve", action="store_true", help="常驻每 30 秒重签刷新")
    a = ap.parse_args()
    m = load_spider()

    while True:
        txt, ok, bad = build(m, a.group)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print("[%s] 生成 %d 个频道 -> %s%s" % (
            time.strftime("%H:%M:%S"), ok, a.out,
            ("  失败: " + ",".join(bad)) if bad else ""))
        if not a.serve:
            break
        time.sleep(30)


if __name__ == "__main__":
    main()