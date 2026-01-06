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

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Tracen_Intel_Center")

ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"

JP_URL = "https://umamusume.jp/news/"
# Глобал часто меняет структуру, используем наиболее стабильный путь к анонсам
GLOBAL_URL = "https://uma.kakaogames.com/news/all" 

DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

# Инициализация клиента
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

if not GROQ_KEY or not WEBHOOK:
    logger.critical("ОШИБКА: Проверьте секреты GitHub (GROQ_API_KEY и DISCORD_WEBHOOK)!")
    sys.exit(1)

client = Groq(api_key=GROQ_KEY)

# ==============================================================================
# МОДУЛЬ СКАНЕРА (УЛУЧШЕННЫЙ)
# ==============================================================================

class TracenScanner:
    def __init__(self, region: str, url: str, db: str):
        self.region = region
        self.url = url
        self.db = db
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def get_latest(self) -> Optional[Dict[str, str]]:
        try:
            r = requests.get(self.url, headers=self.headers, timeout=25)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            
            if "Japan" in self.region:
                # Поиск в японской структуре
                item = soup.select_one('.news-list__item')
                if not item: return None
                
                link_tag = item.find('a')
                if not link_tag: return None
                
                link = "https://umamusume.jp" + link_tag['href']
                img_tag = item.find('img')
                img = img_tag['src'] if img_tag else None
                news_id = link.split('=')[-1]
                
            else:
                # Поиск в глобальной структуре (Kakao)
                item = soup.select_one('.article_list li') or soup.select_one('tr') or soup.select_one('.news_item')
                if not item: return None
                
                link_tag = item.find('a')
                if not link_tag: return None
                
                link = link_tag['href']
                if not link.startswith('http'):
                    link = "https://uma.kakaogames.com" + link
                
                img = None # Глобал редко дает превью в списке
                news_id = link.rstrip('/').split('/')[-1]

            return {"id": str(news_id), "url": link, "img": img}
        except Exception as e:
            logger.error(f"[{self.region}] Ошибка сканирования: {e}")
            return None

    def is_new(self, news_id: str) -> bool:
        if not os.path.exists(self.db):
            with open(self.db, 'w') as f: f.write("0")
            return True
        with open(self.db, 'r') as f:
            return f.read().strip() != news_id

    def save(self, news_id: str):
        with open(self.db, 'w') as f: f.write(news_id)

# ==============================================================================
# ИИ-АНАЛИЗАТОР (УЛЬТРА)
# ==============================================================================

class MultiRegionAI:
    @staticmethod
    def analyze(raw_html: str, region: str) -> Dict[str, Any]:
        # Очистка текста от лишних тегов для экономии токенов
        soup = BeautifulSoup(raw_html, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)[:5000]

        prompt = f"""
        Ты — Главный Аналитик Tracen Academy. Твоя специализация — игра 'Uma Musume: Pretty Derby'.
        РЕГИОН ДАННЫХ: {region}
        
        ЗАДАЧА:
        1. Назначь RANK (S/A/B/C) по важности.
        2. Переведи заголовок на русский.
        3. Это БАННЕР (гача) или важный СЛИВ? (True/False).
        4. Сделай детальный разбор (даты, камни, новые персонажи).
        5. Дай прогноз: что это значит для будущего игры.

        ОТВЕТЬ СТРОГО JSON:
        {{
            "rank": "...", "title": "...", "is_banner": bool,
            "summary": "Краткая суть", "details": "Детальный список",
            "future": "Прогноз", "verdict": "Совет тренеру"
        }}
        """
        try:
            res = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            return json.loads(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"AI Error: {e}")
            return {"rank": "B", "title": "Ошибка анализа", "is_banner": False, 
                    "summary": "Не удалось обработать текст", "details": "N/A", "future": "N/A", "verdict": "N/A"}

# ==============================================================================
# ЛОГИКА ОБРАБОТКИ
# ==============================================================================

def process_region(name, url, db):
    scanner = TracenScanner(name, url, db)
    meta = scanner.get_latest()
    
    if meta and scanner.is_new(meta["id"]):
        logger.info(f"[{name}] Найдена новая запись: {meta['id']}")
        
        # Получение контента статьи
        try:
            content_page = requests.get(meta["url"], timeout=20).text
        except:
            content_page = "Не удалось загрузить страницу."

        analysis = MultiRegionAI.analyze(content_page, name)
        
        # Пинги и оформление
        ping = f"<@&{ROLE_NEWS}>"
        if analysis.get("is_banner") or analysis.get("rank") == "S":
            ping += f" <@&{ROLE_BANNER}>"
            
        color = {"S": 0xFFD700, "A": 0xFF4500, "B": 0xDA70D6, "C": 0x5DADE2}.get(analysis["rank"], 0x99AAB5)
        
        embed_data = {
            "content": f"📢 **ОПЕРАТИВНЫЙ ОТЧЕТ: {name.upper()}**\n{ping}",
            "embeds": [{
                "title": f"— ✦ RANK: {analysis['rank']} | {analysis['title']} ✦ —",
                "description": (
                    f"**{analysis['summary']}**\n\n"
                    f"╭─── ⭐ **АНАЛИЗ ({name})**\n"
                    f"│ {analysis['details']}\n"
                    "│\n"
                    f"│ ▸ **ПРОГНОЗЫ И СЛИВЫ**\n"
                    f"│ 🔮 {analysis['future']}\n"
                    "│\n"
                    f"│ ▸ **ВЕРДИКТ**\n"
                    f"│ ✅ {analysis['verdict']}\n"
                    f"╰─── 🔗 [ОРИГИНАЛ]({meta['url']})"
                ),
                "color": color,
                "image": {"url": meta["img"]} if meta["img"] else {},
                "footer": {"text": f"Region: {name} | Tracen Intelligence Unit"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }]
        }
        
        response = requests.post(WEBHOOK, json=embed_data)
        if response.status_code < 300:
            scanner.save(meta["id"])
            logger.info(f"[{name}] Успешно отправлено.")

def main():
    # Япония
    process_region("Japan", JP_URL, DB_JP)
    time.sleep(5) # Задержка между регионами
    # Глобал
    process_region("Global", GLOBAL_URL, DB_GL)

if __name__ == "__main__":
    main()
