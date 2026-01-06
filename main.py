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
from typing import Dict, Any, Optional, List

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
logger = logging.getLogger("Tracen_Intelligence")

# Настройки Discord
ROLE_NEWS = "1440444308506280210"
ROLE_BANNER = "1439787310831894679"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

# Конфиги URL
JP_URL = "https://umamusume.jp/news/"
GLOBAL_URL = "https://www.crunchyroll.com/news" 
DB_JP = "last_id_jp.txt"
DB_GL = "last_id_gl.txt"

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

class TracenScanner:
    def __init__(self, region_name: str, base_url: str, db_file: str):
        self.region = region_name
        self.url = base_url
        self.db_file = db_file
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    def get_latest_list(self) -> List[Dict[str, str]]:
        try:
            print(f"--- Сканирование {self.region}... ---", flush=True)
            r = requests.get(self.url, headers=self.headers, timeout=25)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            results = []

            if "Japan" in self.region:
                items = soup.select('.news-list__item')[:3]
                for item in items:
                    link_tag = item.find('a')
                    if not link_tag: continue
                    href = link_tag['href']
                    full_link = "https://umamusume.jp" + href if href.startswith('/') else href
                    id_val = re.search(r'id=(\d+)', full_link).group(1) if "id=" in full_link else full_link.split('/')[-1]
                    img = item.find('img')['src'] if item.find('img') else None
                    results.append({"id": str(id_val), "url": full_link, "img": img})
            else:
                links = soup.find_all('a', href=True)
                for a in links:
                    hrf = a['href'].lower()
                    if ("uma-musume" in hrf or "pretty-derby" in hrf) and len(results) < 2:
                        full_link = a['href'] if a['href'].startswith('http') else "https://www.crunchyroll.com" + a['href']
                        results.append({"id": str(full_link.split('/')[-1]), "url": full_link, "img": None})
            return results
        except Exception as e:
            print(f"Ошибка сканера {self.region}: {e}", flush=True)
            return []

    def get_old_ids(self) -> List[str]:
        if not os.path.exists(self.db_file): return []
        with open(self.db_file, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f.readlines() if line.strip()]

    def save_ids(self, ids: List[str]):
        with open(self.db_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(ids))

class MultiRegionAI:
    @staticmethod
    def analyze(html_content: str, region: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html_content, 'html.parser')
        body = soup.select_one('.p-news-detail__body') or soup.select_one('.news-detail__body') or soup.select_one('article')
        text = body.get_text(separator=' ', strip=True) if body else soup.get_text()[:4000]

        prompt = f"Ты главный аналитик Tracen Intelligence. Сделай разбор новости для {region} на русском. ВЕРНИ СТРОГО JSON."
        try:
            res = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": f"{prompt}\n\nТекст новости: {text[:5000]}"}],
                response_format={"type": "json_object"}
            )
            return json.loads(res.choices[0].message.content), text
        except:
            return None, text

def process_region(region_name, url, db_file):
    scanner = TracenScanner(region_name, url, db_file)
    latest_news = scanner.get_latest_list()
    old_ids = scanner.get_old_ids()
    
    processed_ids = []
    # Идем с конца (от более старых к новым), чтобы в Discord они шли в верном порядке
    for meta in reversed(latest_news):
        if meta["id"] not in old_ids:
            print(f"!!! НАЙДЕНА НОВАЯ НОВОСТЬ: {region_name} (ID: {meta['id']}) !!!", flush=True)
            try:
                resp = requests.get(meta["url"], timeout=20)
                resp.encoding = 'utf-8'
                analysis, raw_text = MultiRegionAI.analyze(resp.text, region_name)
                
                if not analysis: continue

                # Проверка на баги для смены цвета
                is_bug = any(word in raw_text.lower() for word in ["不具合", "ошибка", "баг", "bug", "неполадка", "修正"])
                color = 0xFF0000 if is_bug else 0xFF69B4 # Красный если баг, иначе Розовый
                
                ping = f"<@&{ROLE_NEWS}>"
                if is_bug or analysis.get("is_banner") or analysis.get("rank") == "S":
                    ping += f" <@&{ROLE_BANNER}>"
                
                payload = {
                    "content": f"📢 **НОВЫЙ ОТЧЕТ: {region_name.upper()}**\n{ping}",
                    "embeds": [{
                        "title": f"— ✦ {'⚠️ БАГ' if is_bug else 'RANK: ' + analysis.get('rank', 'B')} | {analysis.get('title')} ✦ —",
                        "description": (
                            f"**{analysis.get('summary')}**\n\n"
                            f"╭─── ⭐ **АНАЛИЗ ({region_name})**\n"
                            f"│ {analysis.get('details')}\n"
                            "│\n"
                            f"│ ▸ **ПРЕДСКАЗАНИЕ / СЛИВЫ**\n"
                            f"│ 🔮 {analysis.get('future')}\n"
                            "│\n"
                            f"│ ▸ **ВЕРДИКТ ТРЕНЕРА**\n"
                            f"│ ✅ {analysis.get('verdict')}\n"
                            f"╰─── 🔗 [ИСТОЧНИК]({meta['url']})"
                        ),
                        "color": color,
                        "image": {"url": meta["img"]} if meta["img"] else {},
                        "footer": {"text": f"Unit: Tracen Intel • Region: {region_name} • ID: {meta['id']}"},
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                    }]
                }
                if requests.post(WEBHOOK, json=payload).status_code < 300:
                    processed_ids.append(meta["id"])
                    time.sleep(2)
            except Exception as e:
                print(f"Ошибка обработки {meta['id']}: {e}")

    # Сохраняем все увиденные ID (новые + старые, держим лимит 15 штук)
    new_db = list(dict.fromkeys([m["id"] for m in latest_news] + old_ids))[:15]
    scanner.save_ids(new_db)

if __name__ == "__main__":
    print("=== ЗАПУСК TRACEN PINK SYSTEM ===", flush=True)
    process_region("Japan", JP_URL, DB_JP)
    time.sleep(5)
    process_region("Global", GLOBAL_URL, DB_GL)
    print("=== МОНИТОРИНГ ЗАВЕРШЕН ===", flush=True)
