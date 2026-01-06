import os
import json
import logging
import requests
import time
import datetime
import sys
import re
from bs4 import BeautifulSoup
from groq import Groq
from typing import Dict, Any, Optional, Tuple

# Настройка логирования для GitHub Actions
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
logger = logging.getLogger("Tracen_Intelligence")

# Роли для уведомлений
ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"

# Конфиги URL
JP_URL = "https://umamusume.jp/news/"
GLOBAL_URL = "https://www.crunchyroll.com/news" 

DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

GROQ_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

if not GROQ_KEY or not WEBHOOK:
    print("!!! ОШИБКА: Проверь секреты в GitHub !!!", flush=True)
    sys.exit(1)

client = Groq(api_key=GROQ_KEY)

class TracenScanner:
    def __init__(self, region_name: str, base_url: str, db_file: str):
        self.region = region_name
        self.url = base_url
        self.db_file = db_file
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def get_latest(self) -> Optional[Dict[str, str]]:
        try:
            print(f"--- Сканирование {self.region}... ---", flush=True)
            r = requests.get(self.url, headers=self.headers, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            
            if "Japan" in self.region:
                item = soup.select_one('.news-list__item')
                if not item: item = soup.find('a', href=re.compile(r'detail\.php'))
                if not item: return None
                link_tag = item if item.name == 'a' else item.find('a')
                link = "https://umamusume.jp" + link_tag['href']
                img_tag = item.find('img') if hasattr(item, 'find') else None
                img = img_tag['src'] if img_tag else None
                return {"id": str(link.split('=')[-1]), "url": link, "img": img}
            else:
                links = soup.find_all('a', href=True)
                for a in links:
                    if "uma-musume" in a['href'].lower():
                        l = a['href'] if a['href'].startswith('http') else "https://www.crunchyroll.com" + a['href']
                        return {"id": str(l.split('/')[-1]), "url": l, "img": None}
                return None
        except Exception as e:
            print(f"Ошибка сканера {self.region}: {e}", flush=True)
            return None

    def check_new(self, current_id: str) -> bool:
        if not os.path.exists(self.db_file): return True
        with open(self.db_file, 'r') as f:
            return f.read().strip() != current_id

    def save_id(self, current_id: str):
        with open(self.db_file, 'w') as f: f.write(str(current_id))

class MultiRegionAI:
    @staticmethod
    def analyze(text: str, region: str) -> Dict[str, Any]:
        print(f"--- Запуск ИИ-анализа для {region} ---", flush=True)
        prompt = f"""
        Ты — главный аналитик Tracen Intelligence. Сделай подробный разбор на русском.
        РЕГИОН: {region}
        ЗАДАЧА:
        1. Определи Ранг (S/A/B/C).
        2. Это баннер или важный слив? (True/False).
        3. Сделай разбор.
        ВЕРНИ СТРОГО JSON:
        {{
            "rank": "...", "title": "...", "is_banner": bool,
            "summary": "...", "details": "...", "future": "...", "verdict": "..."
        }}
        Текст: {text[:5000]}
        """
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)

def process_region(region_name, url, db_file):
    scanner = TracenScanner(region_name, url, db_file)
    meta = scanner.get_latest()
    
    if meta and scanner.check_new(meta["id"]):
        print(f"!!! НАЙДЕНА НОВАЯ НОВОСТЬ [{region_name}] !!!", flush=True)
        try:
            raw_text = requests.get(meta["url"], timeout=20).text
            analysis = MultiRegionAI.analyze(raw_text, region_name)
            
            ping = f"<@&{ROLE_NEWS}>"
            if analysis.get("is_banner") or analysis.get("rank") == "S":
                ping += f" <@&{ROLE_BANNER}>"
            
            color = {"S": 0xFFD700, "A": 0xFF4500, "B": 0xDA70D6, "C": 0x5DADE2}.get(analysis["rank"], 0x99AAB5)
            
            payload = {
                "content": f"📢 **НОВЫЙ ОТЧЕТ: РЕГИОН {region_name.upper()}**\n{ping}",
                "embeds": [{
                    "title": f"— ✦ RANK: {analysis['rank']} | {analysis['title']} ✦ —",
                    "description": (
                        f"**{analysis['summary']}**\n\n"
                        f"╭─── ⭐ **АНАЛИЗ ({region_name})**\n"
                        f"│ {analysis['details']}\n"
                        "│\n"
                        f"│ ▸ **ПРЕДСКАЗАНИЕ / СЛИВЫ**\n"
                        f"│ 🔮 {analysis['future']}\n"
                        "│\n"
                        f"│ ▸ **ВЕРДИКТ ТРЕНЕРА**\n"
                        f"│ ✅ {analysis['verdict']}\n"
                        f"╰─── 🔗 [ИСТОЧНИК]({meta['url']})"
                    ),
                    "color": color,
                    "image": {"url": meta["img"]} if meta["img"] else {},
                    "footer": {"text": f"Region: {region_name} | Tracen Intelligence Unit"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }]
            }
            
            if requests.post(WEBHOOK, json=payload).status_code < 300:
                scanner.save_id(meta["id"])
                print("Успешно отправлено.", flush=True)
        except Exception as e:
            print(f"Ошибка: {e}", flush=True)
    else:
        print(f"Новостей для {region_name} нет.", flush=True)

if __name__ == "__main__":
    print("=== STARTING TRACEN BOT ===", flush=True)
    process_region("Japan", JP_URL, DB_JP)
    time.sleep(5)
    process_region("Global", GLOBAL_URL, DB_GL)
    print("=== WORK FINISHED ===", flush=True)
