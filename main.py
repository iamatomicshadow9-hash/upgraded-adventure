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
    print("!!! ОШИБКА: Проверь секреты в GitHub (GROQ_API_KEY, DISCORD_WEBHOOK) !!!", flush=True)
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
            r.encoding = 'utf-8' # Гарантируем правильное чтение японского
            soup = BeautifulSoup(r.text, 'html.parser')
            
            if "Japan" in self.region:
                # Продвинутый поиск JP новостей
                item = soup.select_one('.news-list__item') or \
                       soup.find('a', href=re.compile(r'/news/detail\.php\?id=\d+')) or \
                       soup.select_one('li[class*="news"]')
                
                if not item: return None
                
                link_tag = item if item.name == 'a' else item.find('a')
                if not link_tag: return None
                
                href = link_tag['href']
                full_link = "https://umamusume.jp" + href if href.startswith('/') else href
                
                # Извлекаем ID
                id_match = re.search(r'id=(\d+)', full_link)
                news_id = id_match.group(1) if id_match else full_link.split('/')[-1]
                
                img_tag = item.find('img')
                img_url = img_tag['src'] if img_tag else None
                
                return {"id": str(news_id), "url": full_link, "img": img_url}
            else:
                # Поиск EN новостей
                links = soup.find_all('a', href=True)
                for a in links:
                    txt = a.get_text().lower()
                    hrf = a['href'].lower()
                    if "uma-musume" in hrf or "pretty-derby" in hrf:
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
    def analyze(html_content: str, region: str) -> Dict[str, Any]:
        print(f"--- ИИ анализирует {region} ---", flush=True)
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Берем только тело новости, чтобы не тратить токены ИИ на меню сайта
        main_body = soup.select_one('.p-news-detail__body') or \
                    soup.select_one('.news-detail__body') or \
                    soup.select_one('article')
        
        clean_text = main_body.get_text(separator=' ', strip=True) if main_body else soup.get_text()[:4000]

        prompt = f"""
        Ты — главный аналитик Tracen Intelligence. Сделай подробный разбор на русском.
        РЕГИОН: {region}
        Заголовок статьи часто в начале текста.
        
        ВЕРНИ СТРОГО JSON:
        {{
            "rank": "S/A/B/C", "title": "Заголовок на русском", "is_banner": bool,
            "summary": "Краткая суть", "details": "Список ключевых изменений",
            "future": "Прогноз для игроков", "verdict": "Совет: крутить или копить"
        }}
        Текст: {clean_text[:5000]}
        """
        try:
            res = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            return json.loads(res.choices[0].message.content)
        except Exception as e:
            print(f"Ошибка ИИ: {e}", flush=True)
            return {
                "rank": "B", "title": "Новое обновление", "is_banner": False,
                "summary": "Не удалось провести анализ.", "details": "См. оригинал.",
                "future": "N/A", "verdict": "Проверьте новость вручную."
            }

def process_region(region_name, url, db_file):
    scanner = TracenScanner(region_name, url, db_file)
    meta = scanner.get_latest()
    
    if meta and scanner.check_new(meta["id"]):
        print(f"!!! НАЙДЕНА НОВОСТЬ: {region_name} (ID: {meta['id']}) !!!", flush=True)
        try:
            resp = requests.get(meta["url"], timeout=20)
            resp.encoding = 'utf-8' # ФИКС КОДИРОВКИ
            
            analysis = MultiRegionAI.analyze(resp.text, region_name)
            
            ping = f"<@&{ROLE_NEWS}>"
            if analysis.get("is_banner") or analysis.get("rank") == "S":
                ping += f" <@&{ROLE_BANNER}>"
            
            color = {"S": 0xFFD700, "A": 0xFF4500, "B": 0xDA70D6, "C": 0x5DADE2}.get(analysis["rank"], 0x99AAB5)
            
            payload = {
                "content": f"📢 **НОВЫЙ ОТЧЕТ: РЕГИОН {region_name.upper()}**\n{ping}",
                "embeds": [{
                    "title": f"— ✦ RANK: {analysis.get('rank', 'B')} | {analysis.get('title', 'Update')} ✦ —",
                    "description": (
                        f"**{analysis.get('summary', '')}**\n\n"
                        f"╭─── ⭐ **АНАЛИЗ ({region_name})**\n"
                        f"│ {analysis.get('details', '')}\n"
                        "│\n"
                        f"│ ▸ **ПРЕДСКАЗАНИЕ / СЛИВЫ**\n"
                        f"│ 🔮 {analysis.get('future', '')}\n"
                        "│\n"
                        f"│ ▸ **ВЕРДИКТ ТРЕНЕРА**\n"
                        f"│ ✅ {analysis.get('verdict', '')}\n"
                        f"╰─── 🔗 [ИСТОЧНИК]({meta['url']})"
                    ),
                    "color": color,
                    "image": {"url": meta["img"]} if meta["img"] else {},
                    "footer": {"text": f"Unit: Tracen Intel • Region: {region_name} • ID: {meta['id']}"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }]
            }
            
            if requests.post(WEBHOOK, json=payload).status_code < 300:
                scanner.save_id(meta["id"])
                print(f"Успешно отправлено в Discord.", flush=True)
        except Exception as e:
            print(f"Критическая ошибка обработки: {e}", flush=True)
    else:
        print(f"Обновлений для {region_name} нет.", flush=True)

if __name__ == "__main__":
    print("=== STARTING TRACEN INTELLIGENCE ===", flush=True)
    process_region("Japan", JP_URL, DB_JP)
    time.sleep(5)
    process_region("Global", GLOBAL_URL, DB_GL)
    print("=== WORK FINISHED ===", flush=True)
