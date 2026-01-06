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

# Настройка логирования для GitHub
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Tracen_Intelligence")

# Роли для уведомлений
ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"

# Конфиги URL (JP оригинал и стабильный Global)
JP_URL = "https://umamusume.jp/news/"
GLOBAL_URL = "https://www.crunchyroll.com/news" # Это замена упавшему kakaogames

DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

# Проверка ключей
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

if not GROQ_KEY or not WEBHOOK:
    logger.error("Критическая ошибка: Проверь секреты GROQ_API_KEY и DISCORD_WEBHOOK!")
    sys.exit(1)

client = Groq(api_key=GROQ_KEY)

# ==============================================================================
# МОДУЛЬ СКАНЕРА (ТВОЙ, НО С ФИКСОМ ОШИБОК)
# ==============================================================================

class TracenScanner:
    def __init__(self, region_name: str, base_url: str, db_file: str):
        self.region = region_name
        self.url = base_url
        self.db_file = db_file
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def get_latest(self) -> Optional[Dict[str, str]]:
        try:
            r = requests.get(self.url, headers=self.headers, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            
            if "Japan" in self.region:
                # Фикс для JP: ищем более надежно
                item = soup.select_one('.news-list__item')
                if not item: 
                    item = soup.find('a', href=re.compile(r'detail\.php'))
                
                if not item: return None
                
                # Если нашли тег 'a' напрямую или внутри 'item'
                link_tag = item if item.name == 'a' else item.find('a')
                if not link_tag: return None
                
                link = "https://umamusume.jp" + link_tag['href']
                img_tag = item.find('img') if hasattr(item, 'find') else None
                img = img_tag['src'] if img_tag else None
                news_id = link.split('=')[-1]
                return {"id": str(news_id), "url": link, "img": img}
            
            else:
                # Фикс для Global: ищем упоминания Uma Musume на Crunchyroll
                links = soup.find_all('a', href=True)
                for a in links:
                    if "uma-musume" in a['href'].lower():
                        full_link = a['href'] if a['href'].startswith('http') else "https://www.crunchyroll.com" + a['href']
                        news_id = full_link.rstrip('/').split('/')[-1]
                        return {"id": str(news_id), "url": full_link, "img": None}
                return None
        except Exception as e:
            logger.error(f"Ошибка сканера {self.region}: {e}")
            return None

    def check_new(self, current_id: str) -> bool:
        if not os.path.exists(self.db_file): 
            with open(self.db_file, 'w') as f: f.write("0")
            return True
        with open(self.db_file, 'r') as f:
            return f.read().strip() != current_id

    def save_id(self, current_id: str):
        with open(self.db_file, 'w') as f: f.write(current_id)

# ==============================================================================
# ИИ-АНАЛИЗАТОР И ОТПРАВКА (ТВОЙ ФОРМАТ)
# ==============================================================================

class MultiRegionAI:
    @staticmethod
    def analyze(text: str, region: str) -> Dict[str, Any]:
        prompt = f"""
        Ты аналитик Tracen. Регион: {region}. Сделай разбор новости.
        ВЕРНИ JSON:
        {{
            "rank": "S/A/B/C", "title": "Заголовок на рус", "is_banner": true/false,
            "summary": "суть", "details": "подробности", "future": "прогноз", "verdict": "совет"
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
        logger.info(f"Найдена новая новость {region_name}!")
        
        try:
            raw_text = requests.get(meta["url"], timeout=20).text
            analysis = MultiRegionAI.analyze(raw_text, region_name)
        except Exception as e:
            logger.error(f"Ошибка ИИ {region_name}: {e}")
            return

        ping = f"<@&{ROLE_NEWS}>"
        if analysis.get("is_banner") or analysis.get("rank") == "S":
            ping += f" <@&{ROLE_BANNER}>"
            
        color = {"S": 0xFFD700, "A": 0xFF4500, "B": 0xDA70D6, "C": 0x5DADE2}.get(analysis["rank"], 0x99AAB5)
        
        payload = {
            "content": f"📢 **НОВЫЙ ОТЧЕТ: {region_name.upper()}**\n{ping}",
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
                "footer": {"text": f"Region: {region_name}"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }]
        }
        
        if requests.post(WEBHOOK, json=payload).status_code < 300:
            scanner.save_id(meta["id"])

if __name__ == "__main__":
    logger.info("Запуск бота...")
    process_region("Japan", JP_URL, DB_JP)
    process_region("Global", GLOBAL_URL, DB_GL)
    logger.info("Готово.")
