"""补翻译：修复所有缺少中文摘要或英文总结的论文。"""
import sqlite3
import time
import urllib.parse
import urllib.request
import json

GT_URL = "https://translate.googleapis.com/translate_a/single"

def gt(text, sl, tl):
    if not text or len(text.strip()) < 5:
        return None
    try:
        params = urllib.parse.urlencode({"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text[:4000]})
        req = urllib.request.Request(f"{GT_URL}?{params}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = "".join(seg[0] for seg in data[0] if seg[0])
        return result if len(result) >= 5 else None
    except Exception as e:
        print(f"  翻译失败 ({sl}->{tl}): {e}", flush=True)
        return None

conn = sqlite3.connect("paper_pub.db", timeout=30)
cur = conn.cursor()

rows = cur.execute(
    "SELECT id, abstract FROM papers WHERE zh_abstract IS NULL AND abstract IS NOT NULL"
).fetchall()
print(f"需补中文摘要: {len(rows)} 篇", flush=True)
ok, fail = 0, 0
for i, (pid, ab) in enumerate(rows, 1):
    zh = gt(ab, "en", "zh-CN")
    if zh:
        cur.execute("UPDATE papers SET zh_abstract=? WHERE id=?", (zh, pid))
        ok += 1
    else:
        fail += 1
    if i % 10 == 0:
        conn.commit()
        print(f"  中文摘要进度: {i}/{len(rows)}, 成功={ok}, 失败={fail}", flush=True)
    time.sleep(0.5)
conn.commit()
print(f"中文摘要完成: 成功={ok}, 失败={fail}", flush=True)

rows = cur.execute(
    "SELECT id, core_contribution FROM papers WHERE core_contribution_en IS NULL AND core_contribution IS NOT NULL"
).fetchall()
print(f"\n需补英文总结: {len(rows)} 篇", flush=True)
ok, fail = 0, 0
for i, (pid, cc) in enumerate(rows, 1):
    en = gt(cc, "zh-CN", "en")
    if en:
        cur.execute("UPDATE papers SET core_contribution_en=? WHERE id=?", (en, pid))
        ok += 1
    else:
        fail += 1
    if i % 10 == 0:
        conn.commit()
        print(f"  英文总结进度: {i}/{len(rows)}, 成功={ok}, 失败={fail}", flush=True)
    time.sleep(0.5)
conn.commit()

conn.close()
print(f"英文总结完成: 成功={ok}, 失败={fail}", flush=True)
print("\n全部补翻译完成!", flush=True)
