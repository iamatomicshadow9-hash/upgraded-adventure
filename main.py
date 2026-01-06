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
# КОНФИГУРАЦИЯ И СИСТЕМА ЛОГИРОВАНИЯ
# ==============================================================================

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Tracen_Intelligence_Center")

# ID Ролей для уведомлений
ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"

# Источники данных
JP_URL = "https://umamusume.jp/news/"
# Стабильный источник для EN-новостей (обходит NameResolutionError)
GLOBAL_URL = "https://www.crunchyroll.com/news" 

DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

# Проверка секретов
GROQ_KEY = os.environ.get("GROQ_API_KEY")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

if not GROQ_KEY or not WEBHOOK:
    logger.critical("Критическая ошибка: Проверьте секреты (GROQ_API_KEY и DISCORD_WEBHOOK) в GitHub!")
    sys.exit(1)

client = Groq(api_key=GROQ_KEY)

# ==============================================================================
# МОДУЛЬ ГИБКОГО СКАНЕРА
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
                # Парсинг японского сайта
                item = soup.select_one('.news-list__item')
                if not item: 
                    # Попытка найти альтернативную структуру
                    item = soup.find('a', href=re.compile(r'/news/detail\.php\?id='))
                
                if not item: return None
                
                link_tag = item if item.name == 'a' else item.find('a')
                if not link_tag: return None
                
                link = "https://umamusume.jp" + link_tag['href']
                img_tag = item.find('img') if hasattr(item, 'find') else None
                img = img_tag['src'] if img_tag else None
                news_id = link.split('=')[-1]
                
                return {"id": str(news_id), "url": link, "img": img}
            
            else:
                # Поиск EN-новостей на стабильном агрегаторе
                links = soup.find_all('a', href=True)
                for a in links:
                    href = a['href'].lower()
                    if "uma-musume" in href or "pretty-derby" in href:
                        full_link = a['href']
                        if not full_link.startswith('http'):
                            full_link = "https://www.crunchyroll.com" + full_link
                        
                        # Извлекаем ID из URL (последняя часть пути)
                        news_id = full_link.rstrip('/').split('/')[-1]
                        return {"id": str(news_id), "url": full_link, "img": None}
                return None

        except Exception as e:
            logger.error(f"[{self.region}] Сайт временно недоступен или изменил структуру: {e}")
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
# ИИ-АНАЛИТИК (LLAMA-3.3-70B)
# ==============================================================================

class MultiRegionAI:
    @staticmethod
    def analyze(raw_html: str, region: str) -> Dict[str, Any]:
        soup = BeautifulSoup(raw_html, 'html.parser')
        # Очищаем текст от скриптов и стилей
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=' ', strip=True)[:6000]

        prompt = f"""
        Ты — Главный Аналитик Академии Трэсен. Твоя специализация — Uma Musume.
        РЕГИОН ДАННЫХ: {region} (Анализируй на русском языке).
        
        ТВОЯ ЗАДАЧА:
        1. Назначь RANK (S/A/B/C) по критичности новости.
        2. Переведи заголовок на русский (красиво и понятно).
        3. Это БАННЕР (новая девочка/карта) или важный СЛИВ/АНОНС? (True/False).
        4. Детальный разбор: даты, награды (камни), новые механики.
        5. Прогноз: на что это намекает в будущем?

        ОТВЕТЬ СТРОГО В JSON:
        {{
            "rank": "...", "title": "...", "is_banner": bool,
            "summary": "Суть одной фразой", "details": "Список ключевых фактов",
            "future": "Что ждать дальше?", "verdict": "Совет игроку (крутить/скипать/копить)"
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
            logger.error(f"Ошибка ИИ: {e}")
            return {
                "rank": "B", "title": "Новое обновление", "is_banner": False,
                "summary": "Данные получены, но анализ ИИ временно недоступен.",
                "details": "Пожалуйста, проверьте официальный источник по ссылке ниже.",
                "future": "N/A", "verdict": "Ознакомьтесь с новостью самостоятельно."
            }

# ==============================================================================
# ЛОГИКА ОБРАБОТКИ И ОТПРАВКИ
# ==============================================================================

def process_region(name: str, url: str, db: str):
    scanner = TracenScanner(name, url, db)
    meta = scanner.get_latest()
    
    if meta and scanner.is_new(meta["id"]):
        logger.info(f"[{name}] Найдена новая новость! ID: {meta['id']}")
        
        try:
            r = requests.get(meta["url"], timeout=20)
            page_content = r.text
        except:
            page_content = "Не удалось загрузить текст статьи."

        analysis = MultiRegionAI.analyze(page_content, name)
        
        # Настройка уведомлений
        ping = f"<@&{ROLE_NEWS}>"
        if analysis.get("is_banner") or analysis.get("rank") == "S":
            ping += f" <@&{ROLE_BANNER}>"
            
        color = {"S": 0xFFD700, "A": 0xFF4500, "B": 0xDA70D6, "C": 0x5DADE2}.get(analysis["rank"], 0x99AAB5)
        
        embed_data = {
            "content": f"📢 **НОВЫЙ ОТЧЕТ: {name.upper()}**\n{ping}",
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
                    f"╰─── 🔗 [ОРИГИНАЛ НОВОСТИ]({meta['url']})"
                ),
                "color": color,
                "image": {"url": meta["img"]} if meta["img"] else {},
                "footer": {"text": f"Logic: Llama-3.3-70B • Region: {name}"},
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }]
        }
        
        res = requests.post(WEBHOOK, json=embed_data)
        if res.status_code < 300:
            scanner.save(meta["id"])
            logger.info(f"[{name}] Сообщение успешно доставлено.")

def main():
    # Сначала проверяем Японию
    process_region("Japan", JP_URL, DB_JP)
    # Небольшая пауза для стабильности
    time.sleep(5)
    # Затем проверяем Глобал
    process_region("Global", GLOBAL_URL, DB_GL)

if __name__ == "__main__":
    main()
