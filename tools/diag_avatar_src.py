# -*- coding: utf-8 -*-
"""v4.1.39 头像排查:真实跑下载链 4 个源,逐源打印 status/len/魔数。
用法: python tools/diag_avatar_src.py <icon_id>
"""
import os, sys, io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests
from lcu.connector import LCUConnector as Connector

icon_id = int(sys.argv[1]) if len(sys.argv) > 1 else 7084


def magic(data: bytes) -> str:
    h = data[:16].hex()
    if data[:2] == b'\xff\xd8':
        return 'JPEG'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'PNG'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'WEBP'
    return 'UNKNOWN ' + h


conn = Connector()
ok = conn.find_client(quick=True)
print('discover:', ok, 'port=', getattr(conn, '_port', None), 'connected=', conn.is_connected)
if not ok:
    sys.exit(1)

s = requests.Session()

# 1) LCU 本地资产
for ext in ('.jpg', '.png'):
    url = f'{conn.base_url}/lol-game-data/assets/v1/profileicons/{icon_id}{ext}'
    try:
        r = s.get(url, auth=conn.auth, timeout=5)
        print(f'LCU   {url}\n      -> status={r.status_code} len={len(r.content)} {magic(r.content) if r.content else ""}')
    except Exception as e:
        print(f'LCU   {ext}: {type(e).__name__}: {e}')

# 2) CommunityDragon
url = (f'https://raw.communitydragon.org/latest/plugins/'
       f'rcp-be-lol-game-data/global/default/v1/profile-icons/{icon_id}.jpg')
try:
    r = s.get(url, timeout=10)
    print(f'CDragon {url}\n      -> status={r.status_code} len={len(r.content)} {magic(r.content) if r.content else ""}')
except Exception as e:
    print(f'CDragon: {type(e).__name__}: {e}')

# 3) 腾讯旧 CDN
url = f'https://game.gtimg.cn/images/lol/act/img/profileicon/{icon_id}.png'
try:
    r = s.get(url, timeout=6)
    print(f'gtimg  {url}\n      -> status={r.status_code} len={len(r.content)} {magic(r.content) if r.content else ""}')
except Exception as e:
    print(f'gtimg: {type(e).__name__}: {e}')
