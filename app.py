from flask import Flask, request, jsonify
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import random
from collections import Counter
from operator import itemgetter
from TaiwanLotteryCrawler import TaiwanLotteryCrawler
import json, os

# === 🔐 Monkey Patch: requests.get 全域改用 SSL 免驗證版本 ===
import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
import ssl

class SSLBypassAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

session = requests.Session()
session.mount("https://", SSLBypassAdapter())
requests.get = session.get  # 🧠 Monkey Patch 關鍵！

# === Google Sheets 認證 ===
SPREADSHEET_ID = os.environ['SPREADSHEET_ID']
creds_info = json.loads(os.environ['GOOGLE_SHEET_JSON'])
creds = Credentials.from_service_account_info(creds_info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
client = gspread.authorize(creds)

crawler = TaiwanLotteryCrawler()
now = datetime.now()
start_year = now.year - 10

app = Flask(__name__)

# === 彩券欄位對應 ===
def extract_lotto649(draw, date_str):
    return [date_str, draw.get('期別')] + draw.get('獎號') + [draw.get('特別號')]

def extract_daily539(draw, date_str):
    return [date_str, draw.get('期別')] + draw.get('獎號')

def extract_powerlotto(draw, date_str):
    return [date_str, draw.get('期別')] + draw.get('第一區') + [draw.get('第二區')]

# === 資料抓取與寫入 ===
def fetch_and_write(game_key, sheet_name, extract_func):
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    existing_dates = set(sheet.col_values(1))
    rows = []
    for year in range(start_year, now.year + 1):
        for month in range(1, 13):
            if year == now.year and month > now.month:
                continue
            try:
                results = getattr(crawler, game_key)([str(year), f"{month:02d}"])
                for draw in results:
                    date_str = draw.get('開獎日期') or draw.get('date')
                    if not date_str:
                        continue
                    draw_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    date_str = draw_date.strftime("%Y/%m/%d")
                    if draw_date > now or date_str in existing_dates:
                        continue
                    row = extract_func(draw, date_str)
                    if row:
                        rows.append(row)
                        existing_dates.add(date_str)
            except Exception as e:
                print(f"❌ 抓取失敗 {sheet_name} {year}/{month:02d}：{e}")
                continue
    if rows:
        sheet.append_rows(sorted(rows, key=itemgetter(1)))

# === 推薦號碼產生器 ===
def generate_recommendations_from_sheet(sheet_name, number_count, number_range):
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    records = sheet.get_all_values()[1:]
    columns = ["date", "term"] + [f"num{i}" for i in range(1, number_count + 1)]
    if len(records[0]) > len(columns):
        columns += ["special"]
    df = pd.DataFrame(records, columns=columns)
    all_numbers = df[[f'num{i}' for i in range(1, number_count + 1)]].astype(int).values.flatten()
    number_counts = Counter(all_numbers)
    hot = [num for num, _ in number_counts.most_common(5)]
    cold = [num for num, _ in number_counts.most_common()[-5:]]
    last_seen = {i: 0 for i in range(1, number_range + 1)}
    for i, row in df.iterrows():
        for n in row[2:2 + number_count]:
            last_seen[int(n)] = i
    missing = sorted([(k, len(df) - v) for k, v in last_seen.items()], key=lambda x: -x[1])
    overdue = [num for num, _ in missing[:15]]
    all_pool = set(range(1, number_range + 1))
    exclude = set(hot) | set(cold) | {1, 2, number_range - 1, number_range}
    pool = list((all_pool - exclude) | set(overdue))

    def generate():
        while True:
            pick = sorted(random.sample(pool, number_count))
            if any(abs(pick[i] - pick[i + 1]) == 1 for i in range(number_count - 1)):
                continue
            odd = sum(1 for n in pick if n % 2 == 1)
            if not (2 <= odd <= number_count - 2):
                continue
            if pick[0] < 5 or pick[-1] > number_range - 4:
                continue
            return pick

    picks = [generate() for _ in range(50000)]
    flat = [n for pick in picks for n in pick]
    most_common = [num for num, _ in Counter(flat).most_common(number_count * 2)]
    final_pick = sorted(most_common[:number_count])
    return final_pick

# === API 入口 ===
@app.route("/lotto/update", methods=["POST"])
def update_lotto_data():
    fetch_and_write("lotto649", "大樂透", extract_lotto649)
    fetch_and_write("daily_cash", "今彩539", extract_daily539)
    fetch_and_write("super_lotto", "威力彩", extract_powerlotto)
    return jsonify({"status": "ok", "message": "更新完成"})

@app.route("/lotto/recommend", methods=["POST"])
def recommend():
    today = datetime.now().strftime("%Y/%m/%d")
    games = [
        ("大樂透", 6, 49),
        ("威力彩", 6, 38),
        ("今彩539", 5, 39),
    ]
    result = []
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("推薦號碼")
    for name, count, num_range in games:
        pick = generate_recommendations_from_sheet(name, count, num_range)
        row = [today, name, "統計推薦"] + pick
        result.append(row)
    sheet.append_rows(result)
    return jsonify({"status": "ok", "data": result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
