"""小言 AI 群聊 - pycloudflared 公网隧道"""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("  小言 AI 群聊 - 公网隧道启动中...")
print("=" * 60)
print()
print("pycloudflared 会自动下载 cloudflared 并建立隧道")
print("如看到 https://xxx.trycloudflare.com -> 复制到微信即可")
print("按 Ctrl+C 停止")
print("-" * 60)
sys.stdout.flush()

from pycloudflared import try_cloudflare
try_cloudflare(port=8501)
