import os
import json
import logging
import requests
import time
import datetime
import sys
from bs4 import BeautifulSoup
from groq import Groq
from typing import Dict, Any, Optional, Tuple

# ==============================================================================
# КОНФИГУРАЦИЯ ЦЕНТРА УПРАВЛЕНИЯ
# ==============================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Tracen_Global_JP_Intel")

# Роли для уведомлений
ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"

# Конфиги URL (Пример для Глобала — официальный сайт или агрегатор)
JP_URL = "https://umamusume.jp/news/"
GLOBAL_URL = "https://uma.kakaogames.com/news/" # Пример для глобал/корейской базы

# Файлы БД для каждого региона
DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

# ==============================================================================
# МОДУЛЬ ГЛОБАЛЬНОЙ РАЗВЕДКИ
# ==============================================================================

class TracenScanner:
    def __init__(self, region_name: str, base_url: str, db_file: str):
        self.region = region_name
        self.url = base_url
        self.db_file = db_file
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def get_latest(self) -> Optional[Dict[str, str]]:
        """Универсальный парсер для двух регионов"""
        try:
            r = requests.get(self.url, headers=self.headers, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Логика парсинга (адаптируется под структуру сайта региона)
            if "jp" in self.region.lower():
                item = soup.select_one('.news-list__item')
                link = "https://umamusume.jp" + item.find('a')['href']
                img = item.find('img')['src'] if item.find('img') else None
            else:
                # Пример для глобальной структуры
                item = soup.select_one('.article_list li') or soup.select_one('.news_item')
                link = item.find('a')['href']
                img = None # Глобал часто не дает превью в списке
            
            news_id = link.split('=')[-1] or link.split('/')[-1]
            return {"id": news_id, "url": link, "img": img}
        except Exception as e:
            logger.error(f"Ошибка сканирования {self.region}: {e}")
            return None

    def check_new(self, current_id: str) -> bool:
        if not os.path.exists(self.db_file): return True
        with open(self.db_file, 'r') as f:
            return f.read().strip() != current_id

    def save_id(self, current_id: str):
        with open(self.db_file, 'w') as f: f.write(current_id)

# ==============================================================================
# ИИ-АНАЛИЗАТОР (КРОСС-РЕГИОНАЛЬНЫЙ)
# ==============================================================================

class MultiRegionAI:
    @staticmethod
    def analyze(title: str, text: str, region: str) -> Dict[str, Any]:
        prompt = f"""
        Ты — главный аналитик Tracen Intelligence. 
        РЕГИОН: {region}
        Заголовок: {title}
        Текст: {text}

        ЗАДАЧА:
        1. Определи Ранг (S/A/B/C).
        2. Это баннер или важный слив? (True/False).
        3. Если это Глобал, вспомни, как это было в Японии (если можешь).
        4. Сделай подробный разбор на русском.

        ВЕРНИ JSON:
        {{
            "rank": "...", "title": "...", "is_banner": bool,
            "summary": "...", "details": "...", "future": "...", "verdict": "..."
        }}
        """
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)

# ==============================================================================
# ФИНАЛЬНАЯ СБОРКА И ОТПРАВКА
# ==============================================================================

def process_region(region_name, url, db_file):
    scanner = TracenScanner(region_name, url, db_file)
    meta = scanner.get_latest()
    
    if meta and scanner.check_new(meta["id"]):
        logger.info(f"Найдена новая новость в регионе {region_name}!")
        
        # Получаем полный текст
        r = requests.get(meta["url"])
        soup = BeautifulSoup(r.text, 'html.parser')
        raw_text = soup.get_text()[:6000] # Ограничение для ИИ
        
        analysis = MultiRegionAI.analyze(region_name, raw_text, region_name)
        
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
                "footer": {"text": f"Region: {region_name} | Tracen Intelligence Unit"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }]
        }
        
        requests.post(WEBHOOK, json=payload)
        scanner.save_id(meta["id"])

def main():
    # Запуск по очереди для каждого региона
    process_region("Japan", JP_URL, DB_JP)
    process_region("Global", GLOBAL_URL, DB_GL)

if __name__ == "__main__":
    main()
