#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
import akshare as ak
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
import time

# ==================== 从环境变量读取配置 ====================
EMAIL_ENABLE = os.getenv('EMAIL_ENABLE', 'False').lower() == 'true'
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.qq.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SENDER_EMAIL = os.getenv('SENDER_EMAIL', '')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD', '')
RECEIVER_EMAIL = os.getenv('RECEIVER_EMAIL', '')

# ==================== 策略参数（使用指数代码，参数来自回测）====================
OPTIMAL_PARAMS = {
    '000016': {'short': 3, 'long': 30, 'name': '上证50(000016)'},
    '000300': {'short': 3, 'long': 13, 'name': '沪深300(000300)'},
    '000905': {'short': 3, 'long': 13, 'name': '中证500(000905)'},
    '399006': {'short': 13, 'long': 20, 'name': '创业板指(399006)'},
}

CONFIRM_DAYS = 3

# ==================== 数据获取（指数数据，带重试）====================
def get_latest_data(index_code, days=120, retries=3, delay=5):
    for attempt in range(retries):
        try:
            if index_code.startswith('399'):
                symbol = f"sz{index_code}"
            else:
                symbol = f"sh{index_code}"
            df = ak.stock_zh_index_daily(symbol=symbol)
            df.index = pd.to_datetime(df['date'])
            df = df.sort_index()
            df = df.iloc[-days:]
            df['close'] = df['close'].astype(float)
            return df
        except Exception as e:
            print(f"获取 {index_code} 失败 (尝试 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                print(f"等待 {delay} 秒后重试...")
                time.sleep(delay)
            else:
                print(f"获取 {index_code} 最终失败，跳过")
                return None

# ==================== 信号判断 ====================
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

# ==================== 邮件发送 ====================
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

# ==================== 日志 ====================
def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)

# ==================== 主函数 ====================
def monitor():
    log_message("开始监控ETF信号（指数数据替代）...")
    signals = []
    for code, params in OPTIMAL_PARAMS.items():
        log_message(f"检查 {params['name']} ...")
        sig, price, msg = check_signal(code, params, CONFIRM_DAYS)
        if sig:
            signal_text = f"{params['name']} {sig} 信号！价格: {price:.2f}，{msg}"
            log_message(">>> " + signal_text)
            signals.append(signal_text)
        else:
            log_message(f"{params['name']} 无信号")
    if signals:
        subject = f"【指数信号】{len(signals)}个指数触发信号"
        body = "\n".join(signals) + f"\n\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        send_email(subject, body)
    else:
        log_message("今日无信号")

if __name__ == '__main__':
    monitor()
