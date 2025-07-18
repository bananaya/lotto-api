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
        
    # 整理號碼欄位並轉為整數
    number_cols = [f'num{i}' for i in range(1, number_count + 1)]
    df[number_cols] = df[number_cols].apply(pd.to_numeric, errors='coerce')
    df = df.dropna(subset=number_cols)  # 去除不完整或無法解析的資料
    df[number_cols] = df[number_cols].astype(int)


    # 熱號 / 冷號
    all_numbers = df[number_cols].values.flatten()
    number_counts = Counter(all_numbers)
    hot_numbers = [num for num, _ in number_counts.most_common(10)]
    cold_numbers = [num for num, _ in number_counts.most_common()][-10:]

    # 遺漏號（多久沒出現）
    last_seen = {i: -1 for i in range(1, number_range + 1)}
    for idx, row in df[::-1].iterrows():
        for n in row[number_cols]:
            if last_seen[n] == -1:
                last_seen[n] = len(df) - idx
    overdue_numbers = sorted(last_seen.items(), key=lambda x: -x[1])[:15]
    overdue_numbers = [num for num, _ in overdue_numbers]

    # 平均值 & 標準差
    df['mean'] = df[number_cols].mean(axis=1)
    df['std'] = df[number_cols].std(axis=1)
    focused_df = df[df['std'] < 10]  # 集中期數
    focused_numbers = focused_df[number_cols].values.flatten()
    focused_pool = set(focused_numbers)

    # 整合號碼池
    candidate_pool = (set(hot_numbers) | set(cold_numbers) | set(overdue_numbers) | focused_pool)
    candidate_pool = [num for num in candidate_pool if 3 <= num <= number_range - 2]

    def is_valid_combination(nums):
        nums = sorted(nums)
        # 不允許三連號
        if any(nums[i+1] - nums[i] == 1 and nums[i+2] - nums[i+1] == 1 for i in range(len(nums) - 2)):
            return False
        # 奇偶數比例
        odd = sum(1 for n in nums if n % 2 == 1)
        if not (2 <= odd <= number_count - 2):
            return False
        # 和值落在平均範圍
        total = sum(nums)
        avg_mean = df['mean'].mean()
        avg_std = df['mean'].std()
        if not (avg_mean - avg_std <= total / number_count <= avg_mean + avg_std):
            return False
        return True

    def generate():
        tries = 0
        while tries < 1000:
            pick = sorted(random.sample(candidate_pool, number_count))
            if is_valid_combination(pick):
                return pick
            tries += 1
        return sorted(random.sample(range(1, number_range + 1), number_count))  # fallback

    return generate()

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
        row = [str(today), str(name), "統計推薦"] + [str(int(n)) for n in pick]
        result.append(row)
    sheet.append_rows(result)
    return jsonify({"status": "ok", "data": result})    

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
