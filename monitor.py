#!/usr/bin/env python3
# VERSION_MARKER_20260320_001
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
import akshare as ak
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import os
import time
import json
import requests

EMAIL_ENABLE = os.getenv('EMAIL_ENABLE', 'False').lower() == 'true'
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.qq.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SENDER_EMAIL = os.getenv('SENDER_EMAIL', '')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD', '')
RECEIVER_EMAIL = os.getenv('RECEIVER_EMAIL', '')

OPTIMAL_PARAMS = {
    '000016': {'short': 3, 'long': 30, 'name': '上证50(000016)'},
    '000300': {'short': 3, 'long': 13, 'name': '沪深300(000300)'},
    '000905': {'short': 3, 'long': 13, 'name': '中证500(000905)'},
    '399006': {'short': 13, 'long': 20, 'name': '创业板指(399006)'},
}
CONFIRM_DAYS = 3
POSITION_FILE = 'positions.json'

def load_positions():
    if os.path.exists(POSITION_FILE):
        try:
            with open(POSITION_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取持仓文件失败，将使用空持仓: {e}")
            return {}
    else:
        print(f"持仓文件 {POSITION_FILE} 不存在，请创建")
        return {}

def _normalize_index_df(df):
    if df is None or df.empty:
        return None
    if "日期" in df.columns and "收盘" in df.columns:
        df = df.rename(columns={"日期": "date", "收盘": "close"})
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df.index = pd.to_datetime(df["date"])
    df = df.sort_index()
    df["close"] = df["close"].astype(float)
    return df


def get_latest_data(index_code, days=120, retries=3, delay=5):
	# 设置一个通用的请求头，模拟浏览器，防止简单的 UA 拦截
	# 注意：akshare 部分接口支持传入 requests 参数，但为了兼容性，我们在外层做全局设置或尝试
	session = requests.Session()
	session.headers.update({
		'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
	})
	for attempt in range(retries):
		df = None
		try:
			# 策略调整：优先使用东方财富接口（最稳定），其次新浪，最后腾讯
			# 因为新浪和腾讯对创业板指数的代码格式敏感且容易封锁
			# 1. 尝试东方财富 (index_zh_a_hist)
			# 这个接口通常数据最全，且对创业板支持最好
			try:
				end_date = datetime.now().strftime("%Y%m%d")
				start_date = (datetime.now() - timedelta(days=5000)).strftime("%Y%m%d")
				# 注意：akshare 的此接口可能不支持 session 传入，库内部封装了 requests
				df = ak.index_zh_a_hist(symbol=index_code, period="daily", 
										start_date=start_date, end_date=end_date)
				if df is not None and not df.empty:
					 # 东方财富返回的列名通常是中文，交给 _normalize_index_df 处理
					df = _normalize_index_df(df)
					print(f"[尝试 {attempt+1}] 东方财富接口成功获取 {index_code}")
			except Exception as e:
				print(f"[尝试 {attempt+1}] 东方财富接口失败: {e}")
				df = None
			# 2. 尝试新浪 (兜底)
			if df is None:
				try:
					symbol = f"sz{index_code}" if index_code.startswith('399') else f"sh{index_code}"
					df = ak.stock_zh_index_daily(symbol=symbol)
					df = _normalize_index_df(df)
					if df is not None:
						print(f"[尝试 {attempt+1}] 新浪接口成功获取 {index_code}")
				except Exception as e:
					print(f"[尝试 {attempt+1}] 新浪接口失败: {e}")
					df = None
			# 3. 尝试腾讯 (最后兜底)
			if df is None:
				try:
					symbol = f"sz{index_code}" if index_code.startswith('399') else f"sh{index_code}"
					df = ak.stock_zh_index_daily_tx(symbol=symbol)
					df = _normalize_index_df(df)
					if df is not None:
						print(f"[尝试 {attempt+1}] 腾讯接口成功获取 {index_code}")
				except Exception as e:
					print(f"[尝试 {attempt+1}] 腾讯接口失败: {e}")
					df = None
			# 最终检查
			if df is not None and len(df) > 0:
				return df.iloc[-days:]
			else:
				# 如果还是空，等待后重试
				if attempt < retries - 1:
					print(f"所有源均失败，等待 {delay} 秒后重试...")
					time.sleep(delay)
		except Exception as e:
			print(f"发生未预期错误: {e}")
			if attempt < retries - 1:
				time.sleep(delay)
	print(f"警告：{index_code} 最终获取失败，请检查网络环境是否被防火墙拦截")
	return None

def check_signal(code, params, confirm_days=3):
    df = get_latest_data(code, days=params['long'] + confirm_days + 20)
    if df is None or len(df) < params['long'] + confirm_days:
        return None, None, f"数据不足"

    short_ma = params['short']
    long_ma = params['long']

    df['MA_short'] = df['close'].rolling(window=short_ma).mean()
    df['MA_long'] = df['close'].rolling(window=long_ma).mean()

    current_short = df['MA_short'].iloc[-1]
    current_long = df['MA_long'].iloc[-1]

    if current_short > current_long:
        past_close = df['close'].iloc[-confirm_days:]
        past_long = df['MA_long'].iloc[-confirm_days:]
        if all(past_close > past_long):
            return 'BUY', df['close'].iloc[-1], f"连续{confirm_days}天收盘价高于长期均线"

    if current_short < current_long:
        past_close = df['close'].iloc[-confirm_days:]
        past_long = df['MA_long'].iloc[-confirm_days:]
        if all(past_close < past_long):
            return 'SELL', df['close'].iloc[-1], f"连续{confirm_days}天收盘价低于长期均线"

    return None, None, "无信号"

def send_email(subject, body):
    if not EMAIL_ENABLE:
        print("[邮件未发送] 未启用")
        return
    if not all([SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL]):
        print("[邮件未发送] 邮箱配置不完整")
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("邮件发送成功")
    except Exception as e:
        print(f"邮件发送失败: {e}")

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")

def monitor():
    log_message("开始生成信号报告（基于手动持仓）...")

    positions = load_positions()
    if not positions:
        log_message("⚠️ 持仓文件为空或不存在，将默认所有持仓为0")

    for code in OPTIMAL_PARAMS:
        positions.setdefault(code, 0)

    report_lines = []
    for code, params in OPTIMAL_PARAMS.items():
        log_message(f"检查 {params['name']} ...")
        sig, price, msg = check_signal(code, params, CONFIRM_DAYS)
        current_signal = sig if sig else 'NONE'
        price_str = f"{price:.2f}" if price is not None else "N/A"

        position = positions.get(code, 0)
        position_symbol = "✅ 持有" if position == 1 else "❌ 未持有"

        if current_signal == 'BUY' and position == 0:
            action = " **建议买入**"
        elif current_signal == 'SELL' and position == 1:
            action = " **建议卖出**"
        else:
            action = "✅ 无操作"

        report_lines.append(
            f"〖{params['name']}〗\n"
            f" 当前信号：{current_signal}（价格 {price_str}）\n"
            f" 手动持仓：{position_symbol}\n"
            f" 操作建议：{action}\n"
        )

    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    subject = f"〖ETF信号报告〗{today}"
    body = "高爹，以下是今日信号及操作建议（基于您的手动持仓）：\n\n"
    body += "\n".join(report_lines)
    body += f"\n\n报告时间：{today}\n"
    body += "\n（持仓文件：positions.json，请根据实际买卖自行更新）"

    send_email(subject, body)
    log_message("报告邮件已发送")

if __name__ == '__main__':
    monitor()
