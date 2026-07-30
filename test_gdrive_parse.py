import asyncio
import aiohttp
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    folder_id = '1i532ZAH-9pCNrHsCWThG7A-s3nZgWIVc'
    url = f'https://drive.google.com/drive/folders/{folder_id}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            html = await resp.text()

    # Pattern: match aria-label="FILENAME.ext..." followed closely by ssk='...:FILE_ID-...'
    # Example: aria-label="2 Con Wave...mp3..." ... ssk='5:auSv138:1IyXj_7BxBKuCeA6i13LI01vfejhsNpI6-0-16'
    pattern = r'aria-label="([^"]+?\.(?:mp3|wav|flac|m4a|ogg|aac|wma|opus))[^"]*".*?ssk=\'5:[^:]*:([a-zA-Z0-9_-]{28,40})-'
    items = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
    print("Extracted items:", len(items))
    for name, fid in items:
        print(f"🎵 Title: {name} | ID: {fid}")

if __name__ == '__main__':
    asyncio.run(main())
