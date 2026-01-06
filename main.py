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

# Настройка логирования для GitHub Actions (вывод сразу в консоль)
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
    print("!!! ОШИБКА: Проверь секреты GROQ_API_KEY и DISCORD_WEBHOOK в GitHub !!!", flush=True)
    sys.exit(1)

client = Groq(api_key=GROQ_KEY)

class TracenScanner:
    def __init__(self, region_name: str, base_url: str, db_file: str):
        self.region = region_name
        self.url = base_url
        self.db_file = db_file
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def get_latest(self) -> Optional[Dict[str, str]]:
        try:
            print(f"--- Сканирование {self.region}... ---", flush=True)
            r = requests.get(self.url, headers=self.headers, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            
            if "Japan" in self.region:
                # МЕХАНИЗМ УСИЛЕННОГО ПОИСКА (JP)
                item = soup.select_one('.news-list__item')
                if not item:
                    item = soup.find('a', href=re.compile(r'detail\.php\?id=\d+'))
                if not item:
                    item = soup.select_one('li[class*="news"]')

                if not item: 
                    print(f"DEBUG: На странице JP не найдены новости. Проверь структуру сайта.", flush=True)
                    return None
                
                link_tag = item if item.name == 'a' else item.find('a')
                if not link_tag: return None
                
                href = link_tag['href']
                link = "https://umamusume.jp" + href if href.startswith('/') else href
                
                # Извлекаем чистый цифровой ID
                news_id_match = re.search(r'id=(\d+)', link)
                news_id = news_id_match.group(1) if news_id_match else link.split('/')[-1]
                
                img_tag = item.find('img') if hasattr(item, 'find') else None
                img = img_tag['src'] if img_tag else None
                
                return {"id": str(news_id), "url": link, "img": img}
            
            else:
                # ПОИСК ДЛЯ GLOBAL (CRUNCHYROLL)
                links = soup.find_all('a', href=True)
                for a in links:
                    href = a['href'].lower()
                    if "uma-musume" in href or "pretty-derby" in href:
                        l = a['href'] if a['href'].startswith('http') else "https://www.crunchyroll.com" + a['href']
                        id_val = l.rstrip('/').split('/')[-1]
                        return {"id": str(id_val), "url": l, "img": None}
                return None
        except Exception as e:
            print(f"Ошибка сканера {self.region}: {e}", flush=True)
            return None

    def check_new(self, current_id: str) -> bool:
        if not os.path.exists(self.db_file): 
            with open(self.db_file, 'w') as f: f.write("EMPTY")
            return True
        with open(self.db_file, 'r') as f:
            old_id = f.read().strip()
            print(f"Сравнение для {self.region}: Старый({old_id}) vs Новый({current_id})", flush=True)
            return old_id != current_id

    def save_id(self, current_id: str):
        with open(self.db_file, 'w') as f: f.write(str(current_id))

class MultiRegionAI:
    @staticmethod
    def analyze(text: str, region: str) -> Dict[str, Any]:
        print(f"--- Отправка в ИИ ({region}) ---", flush=True)
        prompt = f"""
        Ты — главный аналитик Tracen Intelligence. Сделай подробный разбор на русском.
        РЕГИОН: {region}
        ЗАДАЧА:
        1. Определи Ранг (S/A/B/C).
        2. Это баннер или важный анонс? (True/False).
        3. Сделай краткий и емкий разбор.
        ВЕРНИ СТРОГО JSON:
        {{
            "rank": "...", "title": "...", "is_banner": bool,
            "summary": "...", "details": "...", "future": "...", "verdict": "..."
        }}
        Текст новости: {text[:4500]}
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
        print(f"!!! ОБНАРУЖЕНА НОВАЯ АКТИВНОСТЬ: [{region_name}] ID {meta['id']} !!!", flush=True)
        try:
            # Очистка текста от HTML тегов для ИИ
            resp = requests.get(meta["url"], timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            clean_text = soup.get_text(separator=' ', strip=True)
            
            analysis = MultiRegionAI.analyze(clean_text, region_name)
            
            # Пинги
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
                    "footer": {"text": f"Unit: Tracen Intel • Region: {region_name} • ID: {meta['id']}"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }]
            }
            
            r = requests.post(WEBHOOK, json=payload)
            if r.status_code < 300:
                scanner.save_id(meta["id"])
                print(f"Отчет {region_name} успешно доставлен в Discord.", flush=True)
            else:
                print(f"Ошибка Discord Webhook: {r.status_code}", flush=True)
        except Exception as e:
            print(f"Ошибка обработки {region_name}: {e}", flush=True)
    else:
        print(f"Обновлений для {region_name} не найдено.", flush=True)

if __name__ == "__main__":
    print("=== ЗАПУСК TRACEN INTELLIGENCE SYSTEM ===", flush=True)
    # Обработка Японии
    process_region("Japan", JP_URL, DB_JP)
    print("--- Ожидание перед сменой региона... ---", flush=True)
    time.sleep(7)
    # Обработка Глобала
    process_region("Global", GLOBAL_URL, DB_GL)
    print("=== ЦИКЛ МОНИТОРИНГА ЗАВЕРШЕН ===", flush=True)
