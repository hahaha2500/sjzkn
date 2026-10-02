# -*- coding: utf-8 -*-
"""Py爬虫测活 v2：批量检测 /storage/emulated/0/TV/py 下所有 type=3 爬虫
两阶段：结构（import→实例化→init，坏=❌fail） / 功能（首页→分类，抖=⚠️warn）
动作：列表页点「测活」→action 守护线程批跑→原生弹窗实时日志→复制/导出"""
import os
import sys
import json
import time
import hashlib
import threading
import traceback
import importlib.util
import inspect
import concurrent.futures as _cf

from base.spider import Spider as _BaseSpider

try:  # 设备 java 互操作，本地/无 java 环境自动降级
    from java import jclass, dynamic_proxy
    from java.lang import Runnable
    _HAS_JAVA = True
except Exception:
    _HAS_JAVA = False

DEFAULT_DIR = "/storage/emulated/0/TV/py"
CMD_RUN = "pycheck::run"
DEFAULT_TIMEOUT = 20          # 单文件整体预算（结构+首页+分类）
LOG_MAX_LINES = 1200
STATUS_ICON = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "fail": "\u274c"}
# 模块级状态：壳子可能每次调用都 new Spider，实例字段会丢（历史铁律）
_STATE = {"log": [], "res": {}, "run": False, "done": 0, "lock": threading.Lock()}


def _errstr(e):
    try:
        return "%s: %s" % (type(e).__name__, str(e)[:150])
    except Exception:
        return type(e).__name__


def _short(msg, n=48):
    s = str(msg or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _start(_st):
    with _STATE["lock"]:
        if _st["run"]:
            return False
        _st["log"] = []
        _st["res"] = {}
        _st["done"] = 0
        _st["run"] = True
    return True


def _put(_st, key, val):
    with _STATE["lock"]:
        _st["res"][key] = val


def _all(_st):
    with _STATE["lock"]:
        return dict(_st["res"])


def _note(_st, line):
    stamp = time.strftime("%H:%M:%S")
    row = "[%s] %s" % (stamp, line)
    with _STATE["lock"]:
        _st["log"].append(row)
        if len(_st["log"]) > LOG_MAX_LINES:
            del _st["log"][:len(_st["log"]) - LOG_MAX_LINES]
    print("[py测活] %s" % line)


def _log_text(_st, d, to):
    with _STATE["lock"]:
        lines = list(_st["log"])
    if not lines:
        lines = ["暂无日志：点「测活」开始。"]
    head = "目录：%s ｜ 超时：%ds ｜ 时间：%s\n%s\n" % (
        d, int(to), time.strftime("%Y-%m-%d %H:%M:%S"), "\u2500" * 46)
    return head + "\n".join(lines)


def _counts(_st):
    r = _all(_st).values()
    return (sum(1 for v in r if v.get("status") == "ok"),
            sum(1 for v in r if v.get("status") == "warn"),
            sum(1 for v in r if v.get("status") == "fail"))


class _NoDialog:
    def update(self, text):
        pass

    def flush(self):
        pass

    def finish(self, title, text):
        print("──[ %s ]──\n%s" % (title, text))


class _UiDialog:
    """已弹 AlertDialog：update/finish 均切 UI 线程；tv 未就绪时缓存待写文本（防首帧丢进度）"""

    def __init__(self, act, holder):
        self._act = act
        self._holder = holder
        self._pending = None

    def _post(self, fn):
        try:
            class Run(dynamic_proxy(Runnable)):
                def run(self):
                    try:
                        fn()
                    except Exception as e:
                        print("[py测活] UI更新异常(已拦截):", _errstr(e))
            self._act.getWindow().getDecorView().post(Run())
        except Exception as e:
            print("[py测活] post失败:", _errstr(e))

    def update(self, text):
        tv = self._holder.get("tv")
        if tv is None:
            self._pending = text
            return
        self._post(lambda t=tv, x=text: t.setText(x))

    def flush(self):
        """弹窗真正建好后调用：补写被缓存的首批文本"""
        tv = self._holder.get("tv")
        if tv is not None and self._pending is not None:
            self._post(lambda t=tv, x=self._pending: t.setText(x))
            self._pending = None

    def finish(self, title, text):
        self.update(text)
        self.flush()


class Spider(_BaseSpider):
    # 默影视/影视仓壳会在 init 之前调用 getDependence，必须存在且返回空（否则整源空白）
    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        try:
            return any(ext in str(url).lower() for ext in
                       ('.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.mpg', '.webm'))
        except Exception:
            return False

    def manualVideoCheck(self):
        return False

    def init(self, extend=""):
        cfg = self._parse_ext(extend)
        self.cfg = cfg
        self.scan_dir = cfg.get("dir") or DEFAULT_DIR
        self.timeout = max(5.0, float(cfg.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT))
        self.recursive = bool(cfg.get("recursive", False))
        self.workers = max(1, min(6, int(cfg.get("workers", 2) or 2)))
        _note(_STATE, "初始化：目录=%s 超时=%ds 并发=%d 待测=%d" % (
            self.scan_dir, int(self.timeout), self.workers, len(self._list_files())))

    def getName(self):
        return "Py爬虫测活"

    def homeContent(self, filter):
        return {"class": [{"type_id": "test", "type_name": "测活"}], "filters": {}}

    def homeVideoContent(self):
        return {"list": []}

    # ------------------------------------------------------------ 列表：一条 action 入口
    def categoryContent(self, tid, pg, filter, extend):
        try:
            files = self._list_files()
            res = _all(_STATE)
            if _STATE["run"]:
                remark = "\u23f3 %d/%d" % (_STATE["done"], len(files))
            elif res:
                ok, warn, fail = _counts(_STATE)
                remark = "\u2705%d \u26a0\ufe0f%d \u274c%d / %d" % (ok, warn, fail, len(files))
            else:
                remark = "共 %d 个 py，点此测活" % len(files)
            return {"page": 1, "pagecount": 1, "limit": 1, "total": 1,
                    "list": [{"vod_id": CMD_RUN, "vod_name": "测活（点此弹窗看日志）",
                              "vod_pic": "", "vod_remarks": remark,
                              "style": {"type": "list", "ratio": 1.1}, "action": CMD_RUN}]}
        except Exception as e:
            print("[py测活] categoryContent错误:", _errstr(e))
            return {"page": 1, "pagecount": 1, "limit": 0, "total": 0, "list": []}

    def action(self, action_str):
        try:
            if str(action_str or "") == CMD_RUN:
                return self._start_batch()
            return self._toast("未知 action: %s" % action_str)
        except Exception as e:
            print("[py测活] action错误:", _errstr(e))
            return self._toast("action异常: %s" % _short(_errstr(e), 60))

    def _toast(self, msg):
        return json.dumps({"code": 0, "msg": msg}, ensure_ascii=False)

    # ------------------------------------------------------------ 批跑
    def _start_batch(self):
        files = self._list_files()
        if not files:
            return self._toast("目录无 py：%s" % self.scan_dir)
        if not _start(_STATE):
            return self._toast("测活进行中，进度见弹窗")

        def worker():
            try:
                self._run_batch(files)
            finally:
                _STATE["run"] = False

        threading.Thread(target=worker, daemon=True, name="pycheck-batch").start()
        return self._toast("开始测活（%d 个，并发 %d）" % (len(files), self.workers))

    def _run_batch(self, files):
        total = len(files)
        _note(_STATE, "── 测活开始：%s（%d 个，并发 %d）──" % (self.scan_dir, total, self.workers))
        dlg = self._live_dialog("\U0001f4cb 测活日志",
                                "开始测活：%d 个爬虫…\n目录：%s" % (total, self.scan_dir))
        dlg.flush()
        done = [0]
        gl = threading.Lock()

        def one(i, f):
            with gl:
                done[0] += 1
                no = done[0]
            _note(_STATE, "(%d/%d) 检测 %s" % (no, total, os.path.basename(f)))
            dlg.update("进度 %d/%d ｜ 超时 %ds/个\n\n%s" % (
                no, total, int(self.timeout), "\n".join(self._tail(10))))
            _STATE["done"] = no
            self.test_file(f)

        if self.workers <= 1:
            for i, f in enumerate(files):
                one(i, f)
        else:
            with _cf.ThreadPoolExecutor(max_workers=self.workers) as ex:
                list(ex.map(lambda p: one(0, p), files))
        ok, warn, fail = _counts(_STATE)
        _note(_STATE, "── 测活完成：\u2705%d \u26a0\ufe0f%d \u274c%d ──" % (ok, warn, fail))
        dlg.finish("\U0001f4cb 测活日志（\u2705%d \u26a0\ufe0f%d \u274c%d）" % (ok, warn, fail),
                   _log_text(_STATE, self.scan_dir, self.timeout) + self._export_tail())

    def _export_tail(self):
        p = self._export()
        return "\n\n报告已保存：%s" % p if p else ""

    def _export(self):
        """把结果写到 目录/pycheck_report.txt（源多了方便在电脑上翻）"""
        try:
            p = os.path.join(self.scan_dir, "pycheck_report.txt")
            res = _all(_STATE)
            rows = ["", "文件名".ljust(44) + "状态  结论"]
            for k in sorted(res.keys()):
                v = res[k]
                rows.append("%-3s %-5s %-42s %s" % (
                    STATUS_ICON.get(v.get("status"), "?"), v.get("status", ""),
                    os.path.basename(k)[:42], v.get("msg", "")[:90]))
            with open(p, "w", encoding="utf-8") as f:
                f.write(_log_text(_STATE, self.scan_dir, self.timeout) + "\n\n" + "\n".join(rows))
            return p
        except Exception:
            return ""

    # ------------------------------------------------------------ 弹窗（照抄 pop.py 反射实现）
    def _live_dialog(self, title, text):
        try:
            act = self._activity()
            if not act:
                print("[py测活] 未取到前台 Activity，降级打印")
                return _NoDialog()
            Builder = jclass("android.app.AlertDialog$Builder")
            TextView = jclass("android.widget.TextView")
            ScrollView = jclass("android.widget.ScrollView")
            LinearLayout = jclass("android.widget.LinearLayout")
            LP = jclass("android.widget.LinearLayout$LayoutParams")
            DialogClick = jclass("android.content.DialogInterface$OnClickListener")
            Toast = jclass("android.widget.Toast")
            ClipData = jclass("android.content.ClipData")
            holder = {}

            class Click(dynamic_proxy(DialogClick)):
                def __init__(self, fn):
                    super().__init__()
                    self.fn = fn

                def onClick(self, dialog, which):
                    try:
                        if self.fn:
                            self.fn()
                    except Exception as e:
                        print("[py测活] 按钮异常(已拦截):", _errstr(e))

            def do_copy():
                try:
                    cm = act.getSystemService("clipboard")
                    cm.setPrimaryClip(ClipData.newPlainText("pylog", str(holder["tv"].getText())))
                    Toast.makeText(act, "日志已复制", 0).show()
                except Exception as e:
                    print("[py测活] 复制失败:", _errstr(e))

            def on_ui():
                try:
                    root = LinearLayout(act)
                    root.setOrientation(LinearLayout.VERTICAL)
                    root.setPadding(36, 20, 36, 20)
                    sv = ScrollView(act)
                    tv = TextView(act)
                    tv.setText(text)
                    tv.setTextSize(13.0)
                    tv.setTextIsSelectable(True)
                    sv.addView(tv)
                    root.addView(sv, LP(-1, -2))
                    Builder(act).setTitle(title).setView(root) \
                        .setNeutralButton("复制全文", Click(do_copy)) \
                        .setNegativeButton("关闭", None).show()
                    holder["tv"] = tv
                except Exception as e:
                    print("[py测活] 弹窗构建失败(已拦截):", _errstr(e))
                    traceback.print_exc()

            class Run(dynamic_proxy(Runnable)):
                def run(self):
                    on_ui()

            act.getWindow().getDecorView().post(Run())
            return _UiDialog(act, holder)
        except Exception as e:
            print("[py测活] 弹窗失败:", _errstr(e))
            return _NoDialog()

    def _activity(self):
        try:
            if not _HAS_JAVA:
                return None
            JClass = jclass("java.lang.Class")
            AT = JClass.forName("android.app.ActivityThread")
            cur = AT.getMethod("currentActivityThread").invoke(None)
            f = AT.getDeclaredField("mActivities")
            f.setAccessible(True)
            for r in f.get(cur).values().toArray():
                rc = r.getClass()
                pf = rc.getDeclaredField("paused")
                pf.setAccessible(True)
                if not pf.getBoolean(r):
                    af = rc.getDeclaredField("activity")
                    af.setAccessible(True)
                    return af.get(r)
        except Exception as e:
            print("[py测活] 取Activity失败:", _errstr(e))
        return None

    # ------------------------------------------------------------ 搜索 / 详情（点具体文件看报告）
    def searchContent(self, key, quick, pg="1"):
        try:
            res = _all(_STATE)
            files = [f for f in self._list_files() if key.lower() in os.path.basename(f).lower()]
            items = [self._to_item(f, res.get(f, {})) for f in files]
            return {"list": items, "page": 1, "pagecount": 1,
                    "limit": len(items), "total": len(items)}
        except Exception as e:
            print("[py测活] searchContent错误:", _errstr(e))
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}

    def detailContent(self, ids):
        try:
            f = str(ids[0])
            r = _all(_STATE).get(f)
            if r is None or getattr(self, "cfg", {}).get("fresh", 1):
                r = self.test_file(f)
            d = r.get("detail", {})
            dep = d.get("deps") or "无"
            contract = d.get("contract") or "未校验"
            lines = [
                "文件：%s" % f,
                "状态：%s %s" % (STATUS_ICON.get(r.get("status"), "\U0001f40d"), self._status_text(r)),
                "失败阶段：%s" % d.get("stage", "-"),
                "耗时：%s 秒" % r.get("cost", "-"),
                "首页分类：%s" % _short(d.get("home_class", "") or "无", 60),
                "测的 tid：%s" % d.get("tid", "-"),
                "分类返回：%s 条" % d.get("cnt", "-"),
                "依赖声明：%s" % dep,
                "协议契约：%s" % contract,
                "结论：%s" % _short(r.get("msg", ""), 120),
                "时间：%s" % r.get("time", "-"),
                "",
                "—— 错误堆栈 ——",
                d.get("trace", "无") or "无",
            ]
            return {"list": [{"vod_id": f, "vod_name": os.path.basename(f), "vod_pic": "",
                              "vod_remarks": _short(r.get("msg", ""), 60),
                              "vod_content": "\n".join(lines)}]}
        except Exception as e:
            print("[py测活] detailContent错误:", _errstr(e))
            return {"list": []}

    def playerContent(self, flag, id, vipFlags):
        return {}

    def liveContent(self, url):
        return ""

    def destroy(self):
        pass

    # ------------------------------------------------------------ 核心测活
    def test_file(self, path):
        """整体跑在守护线程，超时放弃（僵尸线程的迟到结果一律丢弃，不污染后续）。"""
        t0 = time.time()
        box = {}
        done = threading.Event()
        modkey = self._modkey(path)

        def worker():
            try:
                self._pipeline(path, box)
            except BaseException as e:
                box["error"] = _errstr(e)
                box["trace"] = traceback.format_exc()[-1600:]
            finally:
                done.set()

        th = threading.Thread(target=worker, name="pytest-" + os.path.basename(path)[:16],
                              daemon=True)
        th.start()
        th.join(self.timeout)
        cost = round(time.time() - t0, 1)

        if not done.is_set():
            sys.modules.pop(modkey, None)          # 摘除注册，避免污染下一个
            res = {"status": "fail", "cost": cost, "path": path,
                   "msg": "超时(>%ss)：多卡在 %s。网络阻塞或死循环" % (
                       int(self.timeout), box.get("stage", "加载")),
                   "detail": {"stage": box.get("stage", "加载"), "trace": "检测超时，取不到堆栈"}}
        else:
            stage = box.get("stage", "未知")
            if "error" in box:
                msg = "[%s] %s" % (stage, box["error"])
                net = self._looks_like_network_error(msg)
                res = {"status": "warn" if net else "fail", "cost": cost, "path": path,
                       "msg": ("⚠疑似网络抖动：" if net else "❌") + msg,
                       "detail": dict(box, trace=box.get("trace", ""))}
            elif box.get("warn"):
                res = {"status": "warn", "cost": cost, "path": path,
                       "msg": box.get("msg", "结构正常但无数据"), "detail": box}
            else:
                res = {"status": "ok", "cost": cost, "path": path,
                       "msg": box.get("msg", ""), "detail": box}
        res["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _put(_STATE, path, res)
        _note(_STATE, "%s %s -> %s %s" % (STATUS_ICON.get(res["status"], "·"),
                                          os.path.basename(path)[:36], res["status"], res["msg"]))
        return res

    def _pipeline(self, path, out):
        """结构阶段：import→找类→实例化→init（坏=fail）
           功能阶段：homeContent→categoryContent（含网络，网络类异常=warn，逻辑类异常=fail）"""
        out["stage"] = "加载"
        out["tid"] = "-"
        out["cnt"] = "-"
        modkey = self._modkey(path)
        saved_path = list(sys.path)
        here = os.path.dirname(os.path.abspath(path))
        if here not in sys.path:
            sys.path.insert(0, here)          # 被测源内部相对 import 也能找到
        sp = None
        if not os.path.isfile(path):
            raise RuntimeError("文件不存在: %s" % path)

        try:  # ── 结构阶段（等价壳加载）
            spec = importlib.util.spec_from_file_location(modkey, path)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法创建模块加载器（多半是语法错误）")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modkey] = mod
            spec.loader.exec_module(mod)       # 顶层异常 = 代码结构性损坏
            cls = self._find_spider_class(mod)
            if cls is None:
                raise RuntimeError("未找到爬虫类（需 Spider 类 + categoryContent）")
            out["base"] = cls.__name__
            sp = cls()
            deps = self._deps_of(sp)
            out["deps"] = ", ".join(deps) if isinstance(deps, list) and deps else (
                deps if isinstance(deps, str) else "无声明(裸类)")
            try:
                sp.siteKey = modkey
            except Exception:
                pass
            out["stage"] = "init"
            self._call(sp.init, self._file_extend(path))
        except Exception:
            out["stage"] = out.get("stage", "结构")
            out["trace"] = traceback.format_exc()[-1600:]
            sys.modules.pop(modkey, None)
            try:
                if list(sys.path) != saved_path:
                    sys.path[:] = saved_path
            except Exception:
                pass
            raise

        try:  # ── 功能阶段
            out["stage"] = "首页"
            home = self._call(sp.homeContent, True)
            cls_home = (home.get("class") or []) if isinstance(home, dict) else []
            out["home_class"] = self._first_class_name(home)
            tid = self._first_tid(home)
            out["tid"] = tid
            if tid is None:
                out["warn"] = True
                n = len(cls_home)
                hint = ""
                if os.path.isfile(path[:-3] + ".json"):
                    hint = "；已读到同名 .json 作 extend"
                else:
                    hint = "；很可能要 extend 配置：放一个 %s.json 或目录里 ext_config.json" % os.path.basename(path)[:-3]
                out["msg"] = "homeContent 的 class 为空（%d 个分类），无法取 tid%s" % (n, hint)
                return
            out["stage"] = "分类"
            t1 = time.time()
            result = self._call(sp.categoryContent, tid, "1", False, {})
            if self._is_empty_result(result) and (time.time() - t1) < self.timeout * 0.4:
                result = self._call(sp.categoryContent, tid, "1", False, {})   # 只在快速失败时补一次
            ok, warn, msg, cnt = self._check_result(result)
            out["cnt"] = cnt
            con = self._contract(result)
            out["contract"] = con
            if ok and not warn and con.startswith("✅"):
                out["msg"] = msg
            else:
                out["warn"] = True          # 契约不全也算可疑：壳子可能照样显示异常
                out["msg"] = (msg or "分类数据异常") + "｜契约" + con
        except Exception as e:
            msg = _errstr(e)
            net = self._looks_like_network_error(msg)
            out["stage"] = out.get("stage", "功能")
            if net:
                out["warn"] = True
                out["msg"] = "功能阶段网络异常(%s)：不等于代码坏" % msg
            else:
                out["error"] = msg
                out["trace"] = traceback.format_exc()[-1600:]
        finally:
            try:
                if sp is not None and hasattr(sp, "destroy"):
                    self._call(sp.destroy)
            except Exception:
                pass
            sys.modules.pop(modkey, None)
            try:
                if list(sys.path) != saved_path:
                    sys.path[:] = saved_path
            except Exception:
                pass

    # ------------------------------------------------------------ 判定 / 工具
    def _modkey(self, path):
        return "pyscan_" + hashlib.md5(str(path).encode("utf-8")).hexdigest()[:10]

    def _tail(self, n):
        with _STATE["lock"]:
            return list(_STATE["log"])[-n:]

    def _deps_of(self, sp):
        """看爬虫声明的 getDependence 是否返回非空依赖（手机壳装不上会白屏）"""
        try:
            fn = getattr(sp, "getDependence", None)
            if not callable(fn):
                return None
            d = self._call(fn)
            if d is None:
                return None
            return list(d) if isinstance(d, (list, tuple)) else [str(d)]
        except Exception as e:
            return "读取异常(%s)" % _errstr(e)

    def _contract(self, result):
        """轻量契约校验：list/page/pagecount/limit/total 有无 + page 是否 int"""
        if not isinstance(result, dict):
            return "返回非 dict ❌"
        need = ["list", "page", "pagecount"]
        miss = [k for k in need if k not in result]
        extra = []
        if not isinstance(result.get("page", 1), int):
            extra.append("page 非 int")
        if isinstance(result.get("list"), list):
            for it in result["list"][:3]:
                if isinstance(it, dict) and not it.get("vod_id"):
                    extra.append("有卡片缺 vod_id")
                    break
        s = "✅" if not miss and not extra else "⚠"
        if miss:
            s += "缺 " + ",".join(miss)
        if extra:
            s += " " + ";".join(extra)
        return s

    def _is_empty_result(self, result):
        if not isinstance(result, dict):
            return True
        lst = result.get("list")
        if lst is None:
            return True
        if isinstance(lst, list) and len(lst) == 0:
            try:
                if int(result.get("pagecount") or 1) > 1 or int(result.get("total") or 0) > 0:
                    return False
            except Exception:
                pass
            return True
        return False

    def _looks_like_network_error(self, msg):
        kw = ["网络", "timeout", "timed out", "connection", "dns", "resolve", "refused",
              "reset", "unreachable", "urlopen", "requests", "httperror", "ssl",
              "connecttimeout", "sockettimeout", "max retries", "failed to establish"]
        ml = str(msg or "").lower()
        return any(k in ml for k in kw)

    def _find_spider_class(self, mod):
        subs, ducks = [], []
        try:
            items = list(vars(mod).items())
        except Exception:
            items = []
        for name, obj in items:
            if not isinstance(obj, type) or obj is _BaseSpider or name.startswith("_"):
                continue
            try:
                if issubclass(obj, _BaseSpider):
                    subs.append((name, obj))
                elif callable(getattr(obj, "init", None)) and callable(
                        getattr(obj, "categoryContent", None)):
                    ducks.append((name, obj))
            except Exception:
                continue
        for name, cls in subs:
            if name == "Spider":
                return cls
        if subs:
            for name, cls in subs:
                if "categoryContent" in cls.__dict__:
                    return cls
            return subs[0][1]
        for name, cls in ducks:
            if name == "Spider":
                return cls
        return ducks[0][1] if ducks else None

    def _call(self, fn, *args):
        """按签名裁剪参数后调用（兼容 init() / categoryContent(tid) 等各种变体）"""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return fn(*args)
        pos, has_var = 0, False
        for p in sig.parameters.values():
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                pos += 1
            elif p.kind == p.VAR_POSITIONAL:
                has_var = True
        if not has_var and pos < len(args):
            args = args[:pos]
        return fn(*args)

    def _first_tid(self, home):
        try:
            if isinstance(home, dict):
                for it in home.get("class") or []:
                    if isinstance(it, dict):
                        tid = it.get("type_id")
                        if tid is not None and str(tid).strip() != "":
                            return str(tid)
        except Exception:
            pass
        return None

    def _first_class_name(self, home):
        try:
            if isinstance(home, dict):
                cl = home.get("class") or []
                if cl and isinstance(cl[0], dict):
                    return str(cl[0].get("type_name", ""))
        except Exception:
            pass
        return ""

    def _check_result(self, result):
        if not isinstance(result, dict):
            return False, False, "返回类型异常: %s（应为 dict）" % type(result).__name__, 0
        lst = result.get("list")
        if lst is None:
            return False, False, "返回缺 list 字段", 0
        if not isinstance(lst, list):
            return False, False, "list 类型异常: %s" % type(lst).__name__, 0
        cnt = len(lst)
        if cnt == 0:
            return False, True, "list 为空（首页/分类没数据）", 0
        try:
            pc = result.get("pagecount", 1)
        except Exception:
            pc = 1
        try:
            total = result.get("total", "")
        except Exception:
            total = ""
        s = "%d 条 · 共 %s 页" % (cnt, pc)
        if total not in ("", None, 0):
            s += " · total=%s" % total
        return True, False, s, cnt

    def _status_text(self, r):
        return {"ok": "有效", "warn": "可疑", "fail": "无效"}.get(r.get("status"), "未知")

    def _to_item(self, f, r):
        return {"vod_id": f,
                "vod_name": STATUS_ICON.get(r.get("status"), "\U0001f40d") + " " + os.path.basename(f),
                "vod_pic": "", "vod_remarks": _short(r.get("msg", "未测"), 40),
                "style": {"type": "list"}}

    def _parse_ext(self, extend):
        extend = (extend or "").strip()
        if not extend:
            return {}
        if extend.startswith("{"):
            try:
                v = json.loads(extend)
                return v if isinstance(v, dict) else {}
            except Exception:
                return {}
        return {"dir": extend}

    def _file_extend(self, path):
        """被测爬虫 init 需要的 extend：同名 .json 或目录内 ext_config.json"""
        try:
            p = path[:-3] + ".json"
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            cfg = os.path.join(self.scan_dir, "ext_config.json")
            if os.path.isfile(cfg):
                with open(cfg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                v = data.get(os.path.basename(path), "")
                if isinstance(v, dict):
                    return json.dumps(v, ensure_ascii=False)
                return str(v or "")
        except Exception:
            pass
        return ""

    def _list_files(self):
        out = []
        try:
            if self.recursive:
                for root, dirs, names in os.walk(self.scan_dir):
                    dirs[:] = [x for x in dirs if not x.startswith(".") and x != "__pycache__"]
                    for n in sorted(names):
                        if n.endswith(".py") and not n.startswith(("_", ".")):
                            out.append(os.path.join(root, n))
            else:
                for n in sorted(os.listdir(self.scan_dir)):
                    p = os.path.join(self.scan_dir, n)
                    if os.path.isfile(p) and n.endswith(".py") and not n.startswith(("_", ".")):
                        out.append(p)
        except Exception as e:
            print("[py测活] 扫描目录失败:", _errstr(e))
        try:
            me = os.path.abspath(__file__)
            out = [p for p in out if os.path.abspath(p) != me]
        except Exception:
            pass
        return out
