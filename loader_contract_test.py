# -*- coding: utf-8 -*-
"""
webhtv(默影视) 加载层契约体检器  ——  每个新源开工必跑
用法: python3 loader_contract_test.py /workspace/xxx.py
依据: https://github.com/Silent1566/webhtv 源码
  - app.py:12        SourceFileLoader(...).load_module().Spider()   → Spider() 无参
  - chaquo/Spider.java:35  app.callAttr("init", obj, extend)        → 必须有 init
  - chaquo/Spider.java:33  callAttr("getDependence", obj)            → 必须返回可 asList 的 list
  - chaquo/Spider.java:34  obj.put("siteKey", siteKey)               → 注入在基类上，子类别覆盖
  - PyLoader.java:38-44  catch(Throwable) → SpiderNull               → 任何异常=静默全空白
  - base/spider.py    ABCMeta + __new__ 单例 + 自带 requests
"""
import sys, importlib.util, json, io, os, re

FAILS = []
def bad(msg):
    FAILS.append(msg); print('  NG  ' + msg)
def good(msg):
    print('  OK  ' + msg)


def check_static(path):
    src = io.open(path, encoding='utf-8').read()
    print('\n[1] 源码静态检查')
    # 缩进/编码
    if src.startswith('# -*- coding'):
        good('文件头编码声明')
    else:
        bad('缺 # -*- coding: utf-8 -*- 首行')
    # 括号配平（tokenize，跳过字符串/注释）
    try:
        import tokenize as _tk, io as _io
        depth = {'{': 0, '(': 0, '[': 0}
        back = {'}': '{', ')': '(', ']': '['}
        for tok in _tk.generate_tokens(_io.StringIO(src).readline):
            if tok.type == _tk.OP and tok.string in depth:
                depth[tok.string] += 1
            elif tok.type == _tk.OP and tok.string in back:
                depth[back[tok.string]] -= 1
        un = [k for k, v in depth.items() if v != 0]
        if un:
            bad('括号不配平 %s' % depth)
        else:
            good('括号配平(tokenize)')
    except Exception as e:
        bad('括号检查异常 %s' % e)

    # init 方法
    if 'def init(' in src:
        good('存在 init()')
    else:
        bad('★缺 init(extend) —— 壳固定调用，缺了=初始化失败=全空白')
    # __init__ 必须可无参
    import re
    m = re.search(r'def __init__\(self[^)]*\)', src)
    if not m:
        bad('★缺 __init__ —— app.py 走 Spider()')
    elif 'extend' in m.group(0) or '=' in m.group(0):
        bad('★__init__ 带必填参数 %s —— 壳走 Spider() 无参，会 TypeError' % m.group(0))
    else:
        good('__init__ 无参')
    # getDependence
    m2 = re.search(r'def getDependence\(self\):\s*\n\s*return\s+([^\n#]+)', src)
    if not m2:
        bad('★缺 getDependence —— Java init 会 callAttr 它')
    elif m2.group(1).strip() != '[]':
        bad('getDependence 返回 %s —— 会让壳去下载依赖，失败=全空白；应返回 []' % m2.group(1).strip())
    else:
        good('getDependence=[]')
    # siteKey 覆盖风险
    for i, line in enumerate(src.split('\n'), 1):
        ls = line.strip()
        if re.match(r'self\.siteKey\s*=[^=]', ls) and 'getattr' not in ls and 'or ""' not in ls and "or ''" not in ls:
            bad('第%d行 覆盖 self.siteKey —— Java 注入在基类上，覆盖会让 localProxy 失效' % i)
    # 顶层必须能编译
    try:
        compile(src, path, 'exec'); good('语法编译通过')
    except SyntaxError as e:
        bad('语法错误: %s (行%s)' % (e.msg, e.lineno))


def check_runtime(path):
    """严格模拟壳子：抽象基类 + 单例 + siteKey 注入 + 无参 Spider()"""
    print('\n[2] 壳子契约模拟运行（单例+无参+siteKey）')
    class _Base(object):
        _instance = None
        def __init__(self):
            self.extend = ''
            self.siteKey = 'JAVA_INJECTED'
        def __new__(cls, *a, **k):
            if cls._instance:
                return cls._instance
            cls._instance = super().__new__(cls)
            return cls._instance
        def getDependence(self): return []
        def init(self, extend=""): pass

    src = io.open(path, encoding='utf-8').read()
    # 剥掉壳子才有的 base.spider 导入，换成我们的模拟基类
    # 把壳子的 base.spider 双模式导入整段替换为模拟基类（保持其余字节不变）
    src = re.sub(r'try:\n\s+from base\.spider import Spider as \w+\n(?:.*\n)*?\n(?=\S|\Z)',
                 'BaseSpider = _Base\n\n', src, count=1)
    src = re.sub(r'class Spider\(\s*(?:BaseSpider|_BaseSpider)\s*\):',
                 'class Spider(_Base):', src)
    if 'class Spider(_Base)' not in src:
        src = re.sub(r'class Spider\(\s*\w+\s*\):', 'class Spider(_Base):', src, count=1)
    if 'class Spider(_Base)' not in src:
        bad('未能定位 class Spider 继承声明（可能类名/继承写法异常）')
        return

    ns = {'_Base': _Base, '__name__': 'spider_mod'}
    try:
        exec(compile(src, path, 'exec'), ns)
    except Exception as e:
        bad('模块顶层执行失败(=壳子加载即失败) %s: %s' % (type(e).__name__, e))
        return
    if 'Spider' not in ns:
        bad('模块里没有 Spider 类 —— 壳子 .Spider() 会 AttributeError')
        return
    good('模块加载 OK, Spider 存在')
    try:
        S = ns['Spider']()
        good('Spider() 无参实例化 OK')
    except Exception as e:
        bad('★Spider() 无参实例化失败(=壳子加载崩溃) %s: %s' % (type(e).__name__, e))
        return
    # siteKey 保留
    if getattr(S, 'siteKey', '') == 'JAVA_INJECTED':
        good('siteKey 未被覆盖')
    else:
        bad('siteKey 被覆盖成 %r' % getattr(S, 'siteKey', None))
    # init 各形态
    for ext in ['', '{}', '{"host":"https://example.com"}', []]:
        try:
            S.init(ext); good('init(%r) OK' % ext)
        except Exception as e:
            bad('init(%r) 抛异常 %s: %s' % (ext, type(e).__name__, e))
    # 六接口签名与不崩
    calls = [('getName', ()), ('getDependence', ()), ('isVideoFormat', ('http://a/b.m3u8',)),
             ('manualVideoCheck', ()), ('homeContent', (False,)), ('homeVideoContent', ()),
             ('localProxy', ()), ('action', ('x',)), ('liveContent', ('u',))]
    for fn, args in calls:
        try:
            getattr(S, fn)(*args); good('%s() 不崩' % fn)
        except Exception as e:
            bad('%s() 抛异常 %s: %s' % (fn, type(e).__name__, e))
    # localProxy 契约
    try:
        r = S.localProxy()
        if isinstance(r, (list, tuple)):
            if len(r) < 3: bad('localProxy 只返回 %d 位 —— 壳按 list.get(0/1/2) 取，会 IndexOutOfBounds' % len(r))
            else:
                if len(r) >= 4 and r[3] is not None and not isinstance(r[3], dict):
                    bad('localProxy 第4位 header 不是 dict（壳用 asMap 读，字符串会抛异常）')
                else:
                    good('localProxy %d 位, header=%s' % (len(r), type(r[3]).__name__ if len(r) >= 4 else 'None'))
        else:
            bad('localProxy 返回 %s，应为 list/tuple' % type(r).__name__)
    except Exception as e:
        bad('localProxy() 抛异常 %s: %s' % (type(e).__name__, e))


def main():
    if len(sys.argv) < 2:
        print('用法: python3 loader_contract_test.py <spider.py>')
        sys.exit(2)
    for path in sys.argv[1:]:
        print('=' * 60)
        print('体检: %s (%d 字节)' % (path, os.path.getsize(path)))
        print('=' * 60)
        check_static(path)
        check_runtime(path)
    print('\n' + '=' * 60)
    if FAILS:
        print('发现 %d 个问题 —— 空白类问题必须先清零再交付：' % len(FAILS))
        for f in FAILS:
            print('  - ' + f)
        sys.exit(1)
    print('加载层体检全部通过')


if __name__ == '__main__':
    main()