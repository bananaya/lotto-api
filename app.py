import logging
from flask import Flask, request, jsonify
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import random
from collections import Counter
from operator import itemgetter
from TaiwanLottery import TaiwanLotteryCrawler
import json, os
from google.oauth2.service_account import Credentials
import numpy as np

# === 設定 logging ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
                logger.warning(f"抓取失敗 {sheet_name} {year}/{month:02d}：{e}")
                continue
    if rows:
        sheet.append_rows(sorted(rows, key=itemgetter(1)))
        logger.info(f"{sheet_name} 共新增 {len(rows)} 筆資料")
    else:
        logger.info(f"{sheet_name} 無新增資料")

# === 推薦號碼產生器 ===
# 固定亂數種子
random.seed(42)
np.random.seed(42)

def generate_recommendations_from_sheet(sheet_name, number_count, number_range, special_range=None, sample_size=100000):
    logger.info(f"產生推薦號碼：{sheet_name}")
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    records = sheet.get_all_values()[1:]
    columns = ["date", "term"] + [f"num{i}" for i in range(1, number_count + 1)]
    has_special = special_range is not None
    if has_special:
        columns += ["special"]
    df = pd.DataFrame(records, columns=columns)

    number_cols = [f'num{i}' for i in range(1, number_count + 1)]
    df[number_cols] = df[number_cols].apply(pd.to_numeric, errors='coerce')
    df = df.dropna(subset=number_cols)
    df[number_cols] = df[number_cols].astype(int)

    if has_special and "special" in df.columns:
        df['special'] = pd.to_numeric(df['special'], errors='coerce')
        df = df.dropna(subset=['special'])
        df['special'] = df['special'].astype(int)

    all_numbers = df[number_cols].values.flatten()
    number_counts = Counter(all_numbers)
    hot_numbers = [num for num, _ in number_counts.most_common(10)]
    cold_numbers = [num for num, _ in number_counts.most_common()][-10:]

    last_seen = {i: -1 for i in range(1, number_range + 1)}
    for idx, row in df[::-1].iterrows():
        for n in row[number_cols]:
            if last_seen[n] == -1:
                last_seen[n] = len(df) - idx
    overdue_numbers = sorted(last_seen.items(), key=lambda x: -x[1])[:15]
    overdue_numbers = [num for num, _ in overdue_numbers]

    df['mean'] = df[number_cols].mean(axis=1)
    df['std'] = df[number_cols].std(axis=1)
    focused_df = df[df['std'] < 10]
    focused_pool = set(focused_df[number_cols].values.flatten())

    historical_combos = {tuple(sorted(row[number_cols])) for _, row in df.iterrows()}

    def is_valid_combination(nums):
        nums = sorted(nums)
        if any(nums[i+1] - nums[i] == 1 and nums[i+2] - nums[i+1] == 1 for i in range(len(nums) - 2)):
            return False
        odd = sum(1 for n in nums if n % 2 == 1)
        if not (2 <= odd <= number_count - 2):
            return False
        return True

    def choose_special(main_nums):
        if not has_special:
            return None
        special_pool = list(set(range(1, special_range + 1)) - set(main_nums))
        if not special_pool:
            special_pool = list(range(1, special_range + 1))
        if "special" in df.columns:
            special_counts = Counter(df["special"])
            distances = {n: min(abs(n - m) for m in main_nums) for n in special_pool}
            filtered = [n for n in special_pool if distances[n] >= 2]
            candidates = filtered if filtered else special_pool
            weights = [special_counts.get(n, 1) for n in candidates]
            probs = [w / sum(weights) for w in weights]
            return int(np.random.choice(candidates, 1, p=probs)[0])
        else:
            return int(random.choice(special_pool))

    def generate_combo(strategy):
        if strategy == "A":
            pool = list(set(hot_numbers) | focused_pool)
        elif strategy == "B":
            pool = list(set(cold_numbers) | set(range(1, number_range + 1)) - set(hot_numbers))
        elif strategy == "C":
            pool = list(range(1, number_range + 1))
        elif strategy == "D":
            pool = list(set(range(1, number_range + 1)))
        else:
            pool = list(range(1, number_range + 1))

        weights = [number_counts.get(num, 1) for num in pool]
        probabilities = [w / sum(weights) for w in weights]

        tries = 0
        while tries < 5000:
            pick = tuple(sorted(np.random.choice(pool, number_count, replace=False, p=probabilities)))
            if not is_valid_combination(pick):
                tries += 1
                continue
            if strategy == "D" and pick in historical_combos:
                tries += 1
                continue
            return list(pick), choose_special(pick)
        return sorted(random.sample(range(1, number_range + 1), number_count)), choose_special([])

    results = []
    for strategy in ["A", "B", "C", "D"]:
        main_nums, special_num = generate_combo(strategy)
        logger.info(f"策略 {strategy} 推薦號碼：{main_nums} 特別號：{special_num}")
        results.append((main_nums, special_num))
    return results

# === API 入口 ===
@app.route("/lotto/update", methods=["POST"])
def update_lotto_data():
    logger.info("開始更新彩券資料...")
    fetch_and_write("lotto649", "大樂透", extract_lotto649)
    fetch_and_write("daily_cash", "今彩539", extract_daily539)
    fetch_and_write("super_lotto", "威力彩", extract_powerlotto)
    logger.info("✅ 資料更新完成")
    return jsonify({"status": "ok", "message": "更新完成"})

@app.route("/lotto/recommend", methods=["POST"])
def recommend():
    today = datetime.now().strftime("%Y/%m/%d")
    all_data = []

    games = [
        ("大樂透", 6, 49, 49),
        ("威力彩", 6, 38, 8),
        ("今彩539", 5, 39, None)
    ]

    strategy_labels = {
        "A": "熱門號 + 熱門區間 + 有連號",
        "B": "冷號 + 冷門區間 + 無連號",
        "C": "區間平衡 + 餘數分布平均",
        "D": "歷史從未出現組合 + 低中高分布平均"
    }

    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("推薦號碼")

    for game_name, number_count, number_range, special_range in games:        
        results = generate_recommendations_from_sheet(game_name, number_count, number_range, special_range)        
        for idx, (main_nums, special_num) in enumerate(results):
            strategy_key = chr(ord("A") + idx)
            label = strategy_labels.get(strategy_key, f"組合{strategy_key}")
            row = [str(today), game_name, label] + [str(n) for n in main_nums]
            if special_num is not None:
                row.append(str(special_num))
            all_data.append(row)

    logger.info(f"寫入推薦資料，共 {len(all_data)} 筆")
    if all_data:
        sheet.append_rows(all_data)

    return jsonify({"status": "ok", "data": all_data})   

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
