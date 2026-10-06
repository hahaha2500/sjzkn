# -*- coding: utf-8 -*-
import importlib.util
spec=importlib.util.spec_from_file_location("bianfu","/workspace/bat.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp=m.Spider()
ok=[];ng=[]
def t(n,c):
    (ok if c else ng).append(n)
t("getDependence==[]", sp.getDependence()==[])
t("_u 去双斜杠", sp._u("//a.969238.xyz//x/y.jpg")=="https://a.969238.xyz/x/y.jpg")
t("_u 相对", sp._u("/t/a.png")=="https://257915.xyz/t/a.png")
t("_pic 默认代理", sp._pic("https://a/b.jpg").startswith("http://127.0.0.1:"))
t("localProxy 无参不崩", sp.localProxy()[0]==403)
t("localProxy str 参数", sp.localProxy("{'url':'https://a/b.jpg'}")[0] in (200,302))
t("localProxy header dict", isinstance(sp.localProxy({"url":"https://a/b.jpg"})[3],dict))
t("_pagecount", sp._pagecount("page=1&page=1232",1)==1232)
t("isVideoFormat", sp.isVideoFormat("https://x/a.m3u8") is True)
t("init 空串", sp.init("")=={})
t("init host 切换", sp.init('{"host":"https://h.xyz/"}') is not None and sp.site=="https://h.xyz")
sp.init("")
print("PASS %d / NG %d"%(len(ok),len(ng)))
for n in ng: print("NG:",n)
