"""
SQLite 每日备份脚本
用法：python backup.py
建议通过 cron（Linux）或任务计划（Windows）每天执行一次。
保留最近 30 份备份，自动删除更旧的文件。
"""
import os
import shutil
import glob
from datetime import datetime

DB_PATH    = os.getenv("DB_PATH", "email_reply.db")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
KEEP_DAYS  = 30

def run_backup():
    if not os.path.exists(DB_PATH):
        print(f"[备份] 数据库文件不存在: {DB_PATH}")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest  = os.path.join(BACKUP_DIR, f"email_reply_{stamp}.db")

    # SQLite 热备份：先用 VACUUM INTO（SQLite 3.27+），降级到普通 copy
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.execute(f"VACUUM INTO '{dest}'")
        conn.close()
        print(f"[备份] VACUUM INTO → {dest}")
    except Exception:
        shutil.copy2(DB_PATH, dest)
        print(f"[备份] 文件复制 → {dest}")

    # 清理超过 KEEP_DAYS 的旧备份
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "email_reply_*.db")))
    for old in backups[:-KEEP_DAYS]:
        os.remove(old)
        print(f"[备份] 已删除旧备份: {os.path.basename(old)}")

    print(f"[备份] 完成，当前共 {min(len(backups), KEEP_DAYS)} 份备份")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    DB_PATH = os.getenv("DB_PATH", "email_reply.db")
    run_backup()
